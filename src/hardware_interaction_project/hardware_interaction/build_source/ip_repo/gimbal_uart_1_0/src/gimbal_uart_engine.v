`timescale 1ns / 1ps
// Stage8 transport owns command estimates. Geometry is the frozen stage7 core.
module gimbal_uart_engine(
    input wire clk,rst_n,input wire [7:0] control,input wire [31:0] control_diagnostic,
    input wire mode_test,mode_changed,input wire [31:0] interface_profile,
    input wire [31:0] cfg_op,cfg_tag,input wire [511:0] cfg_inputs,
    input wire inject_start,input wire [31:0] inject_word,input wire external_rx,
    input wire [8:0] read_addr,output reg [31:0] read_data,output reg read_valid,
    output wire tx_bit,external_oe,output wire busy,output reg done,
    output wire error,output reg armed,position_valid,fault,
    output wire injection_busy
);
    localparam [3:0] IDLE=0,CALC_WAIT=1,PREP_PAN=2,PREP_TILT=3,
        PREP_STOP=4,WAIT_GAP=5,FRAME=6,SEED_LOAD=7,SEED_MULT=8,
        SEED_DIV_START=9,SEED_DIV_WAIT=10,SEED_SAVE=11,SEED_FINISH=12,CANCEL_WAIT=13;
    localparam integer GAP_CYCLES=5000000,WATCHDOG_CYCLES=200000000;
    reg [3:0] state;
    reg stopping;
    reg [31:0] status,result_tag,complete_count,last_stop_status,stop_reason,active_tag;
    reg signed [31:0] current_steps [0:1],current_angles [0:1];
    reg [31:0] results [0:15],plan [0:15];
    reg [31:0] position_epoch,watchdog_age,frame_count,byte_count;
    reg [31:0] rx_count,rx_error_count,reply_count,last_reply,invalid_packets;
    reg [31:0] monitor_count,monitor_error_count;
    reg [7:0] monitor_ring [0:63];
    reg [63:0] cycles,last_frame_start,last_frame_end,previous_frame_end,last_gap;
    reg frame_seen;
    reg [22:0] gap_remaining;
    reg [7:0] frame_motor,frame_direction,frame_revs;
    reg [15:0] frame_speed,frame_rem;
    reg frame_is_stop,stop_axis;
    reg [3:0] byte_index;
    reg tx_start;
    reg [7:0] tx_data;
    wire tx_busy,tx_done;
    wire reset_command=control==8'd4;
    wire local_rst_n=rst_n && !reset_command;
    wire [31:0] ins [0:15];
    genvar g;
    generate for(g=0;g<16;g=g+1) begin:INPUT_WORDS
        assign ins[g]=cfg_inputs[g*32+:32];
    end endgenerate
    reg core_start,core_ack,core_reset;
    wire core_busy,core_done,core_error;
    wire [31:0] core_out [0:15];
    wire [31:0] core_status,core_tag,core_count;
    gimbal_core core(
        .clk(clk),.rst_n(local_rst_n),.start(core_start),.ack_done(core_ack),.reset_state(core_reset),
        .op(cfg_op),.request_tag(cfg_tag),
        .in0(ins[0]),.in1(ins[1]),.in2(ins[2]),.in3(ins[3]),.in4(ins[4]),.in5(ins[5]),.in6(ins[6]),.in7(ins[7]),
        .in8(ins[8]),.in9(current_steps[0]),.in10(current_steps[1]),.in11(ins[11]),
        .in12(ins[12]),.in13(ins[13]),.in14(ins[14]),.in15(ins[15]),
        .busy(core_busy),.done(core_done),.error(core_error),
        .out0(core_out[0]),.out1(core_out[1]),.out2(core_out[2]),.out3(core_out[3]),
        .out4(core_out[4]),.out5(core_out[5]),.out6(core_out[6]),.out7(core_out[7]),
        .out8(core_out[8]),.out9(core_out[9]),.out10(core_out[10]),.out11(core_out[11]),
        .out12(core_out[12]),.out13(core_out[13]),.out14(core_out[14]),.out15(core_out[15]),
        .status(core_status),.result_tag(core_tag),.completion_count(core_count)
    );
    stage8_uart_tx transmitter(clk,local_rst_n,tx_start,tx_data,tx_bit,tx_busy,tx_done);

    // A separate decoder observes generated waveform bits, not the byte request.
    wire [7:0] monitor_data;
    wire monitor_valid,monitor_error;
    stage8_uart_rx tx_monitor(clk,local_rst_n,tx_bit,monitor_data,monitor_valid,monitor_error);
    (* ASYNC_REG="TRUE" *) reg rx_meta,rx_sync;
    always @(posedge clk) begin
        if(!local_rst_n) begin rx_meta<=1;rx_sync<=1;end
        else begin rx_meta<=external_rx;rx_sync<=rx_meta;end
    end
    reg inject_active,inject_tx_start;
    reg [1:0] inject_index;
    reg [31:0] inject_snapshot;
    reg [7:0] inject_data;
    wire inject_bit,inject_tx_busy,inject_tx_done;
    assign injection_busy=inject_active || inject_start;
    stage8_uart_tx injector(clk,local_rst_n,inject_tx_start,inject_data,inject_bit,inject_tx_busy,inject_tx_done);
    wire selected_rx=mode_test ? (inject_active ? inject_bit : tx_bit) : rx_sync;
    wire [7:0] rx_data;
    wire rx_valid,rx_framing_error;
    stage8_uart_rx receiver(clk,local_rst_n,selected_rx,rx_data,rx_valid,rx_framing_error);
    reg [1:0] reply_state;
    reg [7:0] reply_motor;

    // Only validated 14-bit magnitudes reach the multiplier. A registered,
    // iterative /5 avoids a long combinational seed-angle path at 100 MHz.
    reg signed [31:0] seed_steps [0:1];
    reg signed [31:0] seed_angles [0:1];
    reg seed_axis,seed_negative,seed_div_start;
    reg [13:0] seed_magnitude;
    reg [34:0] seed_product;
    reg [31:0] seed_rounded;
    wire seed_div_busy,seed_div_done;
    wire [63:0] seed_quotient,seed_remainder;
    wire safety_request;
    wire seed_rst_n=local_rst_n && !safety_request;
    gimbal_divide seed_divider(clk,seed_rst_n,seed_div_start,{29'd0,seed_product},64'd5,
        seed_div_busy,seed_div_done,seed_quotient,seed_remainder);

    assign busy=state!=IDLE || stopping || injection_busy;
    assign error=status!=0;
    assign external_oe=rst_n && armed && !mode_test;
    wire watchdog_expired=armed && watchdog_age>=WATCHDOG_CYCLES-1 && control!=8'd128 && control!=8'd8;
    assign safety_request=control==8'd16 || control==8'd32 || watchdog_expired || (armed && monitor_error);
    wire [31:0] safety_reason=monitor_error ? 32'd20 : (watchdog_expired ? 32'd17 : 32'd18);
    integer i;
    function [7:0] packet_byte;
        input [3:0] index;
        begin
            case(index)
                0:packet_byte=8'h57;
                1:packet_byte=8'h01;
                2:packet_byte=frame_motor;
                3:packet_byte=frame_direction;
                4:packet_byte=frame_speed[15:8];
                5:packet_byte=frame_speed[7:0];
                6:packet_byte=frame_revs;
                7:packet_byte=frame_rem[15:8];
                8:packet_byte=frame_rem[7:0];
                default:packet_byte=8'h09;
            endcase
        end
    endfunction
    task clear_results;
        begin for(i=0;i<16;i=i+1) results[i]<=0;end
    endtask
    task finish_operation;
        input [31:0] code;
        begin
            state<=IDLE;done<=1;status<=code;result_tag<=active_tag;complete_count<=complete_count+1'b1;
            if(code!=0) clear_results;
        end
    endtask
    always @* begin
        read_data=0;read_valid=1;
        if(read_addr>=9'h060 && read_addr<=9'h09c) read_data=results[(read_addr-9'h060)>>2];
        else if(read_addr>=9'h100 && read_addr<=9'h13c) begin
            read_data={monitor_ring[(read_addr-9'h100)+3],monitor_ring[(read_addr-9'h100)+2],
                monitor_ring[(read_addr-9'h100)+1],monitor_ring[read_addr-9'h100]};
        end else case(read_addr)
            9'h0a0:read_data=status;
            9'h0a4:read_data=result_tag;
            9'h0a8:read_data=complete_count;
            9'h0ac:read_data=invalid_packets;
            9'h0b4:read_data={26'd0,mode_test,stopping,fault,external_oe,position_valid,armed};
            9'h0bc:read_data=watchdog_age;
            9'h0c0:read_data=current_steps[0];
            9'h0c4:read_data=current_steps[1];
            9'h0c8:read_data=current_angles[0];
            9'h0cc:read_data=current_angles[1];
            9'h0d0:read_data=frame_count;
            9'h0d4:read_data=byte_count;
            9'h0d8:read_data=rx_count;
            9'h0dc:read_data=rx_error_count;
            9'h0e0:read_data=last_reply;
            9'h0e4:read_data=reply_count;
            9'h0e8:read_data=position_epoch;
            9'h0ec:read_data=last_stop_status;
            9'h0f0:read_data=monitor_count;
            9'h0f4:read_data=monitor_error_count;
            9'h150:read_data=last_frame_start[31:0];
            9'h154:read_data=last_frame_start[63:32];
            9'h158:read_data=last_frame_end[31:0];
            9'h15c:read_data=last_frame_end[63:32];
            9'h160:read_data=previous_frame_end[31:0];
            9'h164:read_data=previous_frame_end[63:32];
            9'h168:read_data=last_gap[31:0];
            9'h16c:read_data=last_gap[63:32];
            default:read_valid=0;
        endcase
    end
    always @(posedge clk) begin
        if(!local_rst_n) begin
            state<=IDLE;stopping<=0;done<=0;armed<=0;position_valid<=0;fault<=0;
            status<=0;result_tag<=0;complete_count<=0;active_tag<=0;stop_reason<=0;last_stop_status<=0;
            for(i=0;i<2;i=i+1) begin current_steps[i]<=0;current_angles[i]<=0;seed_steps[i]<=0;seed_angles[i]<=0;end
            for(i=0;i<16;i=i+1) begin results[i]<=0;plan[i]<=0;end
            for(i=0;i<64;i=i+1) monitor_ring[i]<=0;
            position_epoch<=0;watchdog_age<=0;frame_count<=0;byte_count<=0;
            rx_count<=0;rx_error_count<=0;reply_count<=0;last_reply<=0;invalid_packets<=0;
            monitor_count<=0;monitor_error_count<=0;cycles<=0;last_frame_start<=0;last_frame_end<=0;
            previous_frame_end<=0;last_gap<=0;frame_seen<=0;gap_remaining<=0;
            frame_motor<=1;frame_direction<=0;frame_revs<=0;frame_speed<=0;frame_rem<=0;frame_is_stop<=0;stop_axis<=0;
            byte_index<=0;tx_start<=0;tx_data<=0;core_start<=0;core_ack<=0;core_reset<=0;
            inject_active<=0;inject_tx_start<=0;inject_index<=0;inject_snapshot<=0;inject_data<=0;
            reply_state<=0;reply_motor<=0;seed_axis<=0;seed_negative<=0;seed_magnitude<=0;seed_product<=0;
            seed_div_start<=0;seed_rounded<=0;
        end else begin
            cycles<=cycles+1'b1;tx_start<=0;core_start<=0;core_ack<=0;core_reset<=0;
            seed_div_start<=0;inject_tx_start<=0;
            if(gap_remaining!=0) gap_remaining<=gap_remaining-1'b1;
            if(armed && watchdog_age<WATCHDOG_CYCLES) watchdog_age<=watchdog_age+1'b1;
            if(control==8'd128 || control==8'd8) watchdog_age<=0;
            if(control_diagnostic!=0) last_stop_status<=control_diagnostic;
            if(mode_changed) begin position_valid<=0;position_epoch<=position_epoch+1'b1;end
            if(monitor_valid) begin
                monitor_ring[monitor_count[5:0]]<=monitor_data;monitor_count<=monitor_count+1'b1;
            end
            if(monitor_error) begin
                monitor_error_count<=monitor_error_count+1'b1;fault<=1;position_valid<=0;last_stop_status<=20;
                if(!armed) position_epoch<=position_epoch+1'b1;
            end
            if(rx_valid) begin
                rx_count<=rx_count+1'b1;
                case(reply_state)
                    0:if(rx_data==8'h75) reply_state<=1;
                    1:if(rx_data==1 || rx_data==2) begin reply_motor<=rx_data;reply_state<=2;end
                        else begin invalid_packets<=invalid_packets+1'b1;reply_state<=rx_data==8'h75 ? 1 : 0;end
                    2:if(rx_data==1) reply_state<=3;
                        else begin invalid_packets<=invalid_packets+1'b1;reply_state<=rx_data==8'h75 ? 1 : 0;end
                    3:if(rx_data==3) begin
                            last_reply<={8'h03,8'h01,reply_motor,8'h75};reply_count<=reply_count+1'b1;reply_state<=0;
                        end else begin invalid_packets<=invalid_packets+1'b1;reply_state<=rx_data==8'h75 ? 1 : 0;end
                endcase
            end
            if(rx_framing_error) begin
                rx_error_count<=rx_error_count+1'b1;
                if(reply_state!=0) invalid_packets<=invalid_packets+1'b1;
                reply_state<=0;
            end
            if(inject_start) begin
                inject_active<=1;inject_snapshot<=inject_word;inject_index<=0;inject_data<=inject_word[7:0];inject_tx_start<=1;
            end else if(inject_active && inject_tx_done) begin
                if(inject_index==3) inject_active<=0;
                else begin
                    inject_index<=inject_index+1'b1;inject_data<=inject_snapshot[(inject_index+1'b1)*8+:8];inject_tx_start<=1;
                end
            end
            // Accepted serializer start is the actual first-start-bit edge.
            if(state==FRAME && tx_start && !tx_busy && byte_index==0) begin
                last_frame_start<=cycles;previous_frame_end<=last_frame_end;
                last_gap<=frame_seen ? cycles-last_frame_end : 0;
            end
            if(!safety_request || stopping || state==FRAME) case(state)
                IDLE:begin
                    if(control==8'd1) begin
                        active_tag<=cfg_tag;done<=0;clear_results;
                        if(!armed) begin
                            status<=13;result_tag<=cfg_tag;done<=1;complete_count<=complete_count+1'b1;
                        end else if(!position_valid || fault) begin
                            status<=14;result_tag<=cfg_tag;done<=1;complete_count<=complete_count+1'b1;
                        end else if(ins[9]!=0 || ins[10]!=0) begin
                            status<=15;result_tag<=cfg_tag;done<=1;complete_count<=complete_count+1'b1;
                        end else if(ins[11]<3 || ins[11]>65535) begin
                            status<=12;result_tag<=cfg_tag;done<=1;complete_count<=complete_count+1'b1;
                        end else begin status<=0;core_start<=1;state<=CALC_WAIT;end
                    end else if(control==8'd2) begin done<=0;status<=0;end
                    else if(control==8'd8) begin armed<=1;watchdog_age<=0;end
                    else if(control==8'd64) begin
                        seed_steps[0]<=ins[9];seed_steps[1]<=ins[10];seed_axis<=0;state<=SEED_LOAD;
                    end
                end
                CALC_WAIT:if(core_done) begin
                    core_ack<=1;
                    if(core_error) finish_operation(core_status);
                    else begin
                        for(i=0;i<16;i=i+1) begin plan[i]<=core_out[i];results[i]<=core_out[i];end
                        state<=PREP_PAN;
                    end
                end
                PREP_PAN:if(plan[6]==0) begin
                        current_steps[0]<=plan[4];current_angles[0]<=plan[14];state<=PREP_TILT;
                    end else begin
                        frame_motor<=1;frame_direction<=plan[8][7:0];frame_speed<=ins[11][15:0];
                        frame_revs<=plan[10][7:0];frame_rem<=plan[11][15:0];frame_is_stop<=0;state<=WAIT_GAP;
                    end
                PREP_TILT:if(plan[7]==0) begin
                        current_steps[1]<=plan[5];current_angles[1]<=plan[15];finish_operation(0);
                    end else begin
                        frame_motor<=2;frame_direction<=plan[9][7:0];frame_speed<=ins[11][15:0];
                        frame_revs<=plan[12][7:0];frame_rem<=plan[13][15:0];frame_is_stop<=0;state<=WAIT_GAP;
                    end
                PREP_STOP:begin
                    frame_motor<=stop_axis ? 2 : 1;frame_direction<=0;frame_speed<=0;frame_revs<=0;frame_rem<=0;
                    frame_is_stop<=1;state<=WAIT_GAP;
                end
                WAIT_GAP:if(gap_remaining==0 && !inject_active && !inject_tx_busy) begin
                    byte_index<=0;tx_data<=8'h57;tx_start<=1;state<=FRAME;
                end
                FRAME:if(tx_done) begin
                    byte_count<=byte_count+1'b1;
                    if(byte_index!=9) begin byte_index<=byte_index+1'b1;tx_data<=packet_byte(byte_index+1'b1);tx_start<=1;end
                    else begin
                        // tx_done is registered one cycle after the real edge.
                        last_frame_end<=cycles-1'b1;frame_seen<=1;gap_remaining<=GAP_CYCLES;
                        frame_count<=frame_count+1'b1;
                        if(!frame_is_stop) begin
                            position_epoch<=position_epoch+1'b1;
                            if(frame_motor==1) begin current_steps[0]<=plan[4];current_angles[0]<=plan[14];end
                            else begin current_steps[1]<=plan[5];current_angles[1]<=plan[15];end
                        end
                        if(frame_is_stop) begin
                            if(!stop_axis) begin stop_axis<=1;state<=PREP_STOP;end
                            else begin
                                armed<=0;stopping<=0;position_valid<=0;fault<=1;last_stop_status<=stop_reason;
                                finish_operation(stop_reason);
                            end
                        end else if(stopping || safety_request) begin stop_axis<=0;state<=PREP_STOP;end
                        else if(frame_motor==1) state<=PREP_TILT;
                        else finish_operation(0);
                    end
                end
                SEED_LOAD:begin
                    seed_negative<=seed_steps[seed_axis]<0;
                    seed_magnitude<=seed_steps[seed_axis]<0 ? -seed_steps[seed_axis] : seed_steps[seed_axis];state<=SEED_MULT;
                end
                SEED_MULT:begin seed_product<=seed_magnitude*21'd1179648;state<=SEED_DIV_START;end
                SEED_DIV_START:begin seed_div_start<=1;state<=SEED_DIV_WAIT;end
                SEED_DIV_WAIT:if(seed_div_done) begin
                    seed_rounded<=seed_quotient[31:0]+(seed_remainder>=3);state<=SEED_SAVE;
                end
                SEED_SAVE:begin
                    seed_angles[seed_axis]<=seed_negative ? -seed_rounded : seed_rounded;
                    if(!seed_axis) begin seed_axis<=1;state<=SEED_LOAD;end else state<=SEED_FINISH;
                end
                SEED_FINISH:begin
                    current_steps[0]<=seed_steps[0];current_steps[1]<=seed_steps[1];
                    current_angles[0]<=seed_angles[0];current_angles[1]<=seed_angles[1];
                    position_valid<=1;fault<=0;position_epoch<=position_epoch+1'b1;state<=IDLE;
                end
                CANCEL_WAIT:if(!inject_active && !inject_tx_busy) finish_operation(stop_reason);
            endcase
            // Safety arbitration supersedes calculator/unsent frames/results.
            // An active movement frame is completed intact, then both STOPs.
            if(safety_request && !stopping && (armed || busy)) begin
                position_valid<=0;position_epoch<=position_epoch+1'b1;fault<=1;last_stop_status<=safety_reason;
                active_tag<=(state>=CALC_WAIT && state<=FRAME) ? active_tag : cfg_tag;
                stop_reason<=safety_reason;done<=0;core_reset<=1;clear_results;
                if(armed) begin
                    stopping<=1;stop_axis<=0;
                    if(state!=FRAME) state<=PREP_STOP;
                end else begin
                    state<=CANCEL_WAIT;armed<=0;stopping<=0;
                end
            end
        end
    end
endmodule
