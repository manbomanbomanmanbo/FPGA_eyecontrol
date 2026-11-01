`timescale 1ns / 1ps
// Stateless, compensated laser pan/tilt planning. No UART, motor or encoder I/O.
module gimbal_core(
    input wire clk,rst_n,start,ack_done,reset_state,
    input wire [31:0] op,request_tag,
    input wire [31:0] in0,in1,in2,in3,in4,in5,in6,in7,in8,in9,in10,in11,in12,in13,in14,in15,
    output reg busy,done,output wire error,
    output reg [31:0] out0,out1,out2,out3,out4,out5,out6,out7,out8,out9,out10,out11,out12,out13,out14,out15,
    output reg [31:0] status,result_tag,completion_count
);
    localparam [5:0] IDLE=0,VALIDATE=1,RELATIVE=2,ZCHECK=3,
        SQ_LOAD=4,SQ_MULT=5,SQ_SAVE=6,H_COMPUTE=7,H_CHECK=8,V_CHECK=9,
        ROOT_LOAD=10,ROOT_WAIT=11,RAY_CHECK=12,CORD_LOAD=13,CORD_WAIT=14,
        ANGLES=15,LIMIT_CHECK=16,JOB_LOAD=17,JOB_MULT=18,JOB_ABS=19,
        DIV_LAUNCH=20,DIV_WAIT=21,DIV_ROUND=22,DIV_SIGN=23,JOB_SAVE=24,
        INTERVAL=25,CLAMP=26,DELTA=27,DELTA_ABS=28,PROTOCOL=29,FINISH=30;
    reg [5:0] state;
    reg [31:0] req_op;
    reg signed [31:0] r [0:15];
    reg signed [32:0] x,y,z,sq_operand,delta_pan,delta_tilt;
    reg [65:0] sq_product,squares [0:4];
    reg [2:0] sq_index;
    reg signed [71:0] h_rad,v_rad;
    reg root_kind;
    reg [39:0] forward_q28,s_q28;
    reg signed [43:0] ray_q28;
    reg [1:0] cord_job;
    reg signed [47:0] cangles [0:2],pan_angle_q32,tilt_angle_q32;
    reg signed [31:0] lower_pan,upper_pan,lower_tilt,upper_tilt;
    reg signed [31:0] rounded_pan,rounded_tilt,target_pan,target_tilt,estimate_pan,estimate_tilt;
    reg [32:0] abs_delta_pan,abs_delta_tilt;
    reg [31:0] pan_rev,pan_rem,tilt_rev,tilt_rem;
    reg [3:0] div_job;
    reg signed [63:0] job_base,job_product,job_result;
    reg [63:0] job_num,job_den,rounded_magnitude;
    reg job_negative,round_increment;
    reg [1:0] round_mode; // 0 ties-even, 1 ceil, 2 floor, 3 truncate
    reg sqrt_start,cord_start,divide_start;
    reg [79:0] sqrt_input;
    reg signed [79:0] cord_x,cord_y;
    wire sqrt_busy,sqrt_done,cord_busy,cord_done,divide_busy,divide_done;
    wire [39:0] sqrt_result;
    wire signed [47:0] cord_result;
    wire [63:0] divide_quotient,divide_remainder;
    wire helpers_rst_n=rst_n && !reset_state;
    wire [64:0] twice_remainder={divide_remainder,1'b0};
    integer i;
    assign error=status!=0;
    gimbal_isqrt sqrt_unit(clk,helpers_rst_n,sqrt_start,sqrt_input,sqrt_busy,sqrt_done,sqrt_result);
    gimbal_cordic cordic_unit(clk,helpers_rst_n,cord_start,cord_x,cord_y,cord_busy,cord_done,cord_result);
    gimbal_divide divide_unit(clk,helpers_rst_n,divide_start,job_num,job_den,divide_busy,divide_done,divide_quotient,divide_remainder);

    function signed [31:0] angle_to_q22;
        input signed [47:0] a;
        reg [47:0] m;
        reg [37:0] q;
        reg inc;
        begin
            m=a<0 ? -a : a;q=m>>10;
            inc=m[9:0]>10'd512 || (m[9:0]==10'd512 && q[0]);
            q=q+inc;angle_to_q22=a<0 ? -q[31:0] : q[31:0];
        end
    endfunction
    task clear_outputs;
        begin
            out0<=0;out1<=0;out2<=0;out3<=0;out4<=0;out5<=0;out6<=0;out7<=0;
            out8<=0;out9<=0;out10<=0;out11<=0;out12<=0;out13<=0;out14<=0;out15<=0;
        end
    endtask
    task complete;
        input [31:0] code;
        begin
            status<=code;busy<=0;done<=1;state<=IDLE;completion_count<=completion_count+1'b1;
            if(code!=0) clear_outputs;
        end
    endtask
    always @(posedge clk) begin
        if(!rst_n || reset_state) begin
            busy<=0;done<=0;state<=IDLE;status<=0;result_tag<=0;completion_count<=0;
            req_op<=0;clear_outputs;
            for(i=0;i<16;i=i+1) r[i]<=0;
            for(i=0;i<5;i=i+1) squares[i]<=0;
            for(i=0;i<3;i=i+1) cangles[i]<=0;
            x<=0;y<=0;z<=0;sq_operand<=0;sq_product<=0;sq_index<=0;h_rad<=0;v_rad<=0;
            root_kind<=0;forward_q28<=0;s_q28<=0;ray_q28<=0;cord_job<=0;
            pan_angle_q32<=0;tilt_angle_q32<=0;lower_pan<=0;upper_pan<=0;lower_tilt<=0;upper_tilt<=0;
            rounded_pan<=0;rounded_tilt<=0;target_pan<=0;target_tilt<=0;estimate_pan<=0;estimate_tilt<=0;
            delta_pan<=0;delta_tilt<=0;abs_delta_pan<=0;abs_delta_tilt<=0;
            pan_rev<=0;pan_rem<=0;tilt_rev<=0;tilt_rem<=0;div_job<=0;
            job_base<=0;job_product<=0;job_result<=0;job_num<=0;job_den<=0;rounded_magnitude<=0;
            job_negative<=0;round_increment<=0;round_mode<=0;
            sqrt_start<=0;cord_start<=0;divide_start<=0;sqrt_input<=0;cord_x<=0;cord_y<=0;
        end else begin
            sqrt_start<=0;cord_start<=0;divide_start<=0;
            if(ack_done && done && !busy) begin done<=0;status<=0;end
            if(start && !busy && !done) begin
                busy<=1;status<=0;req_op<=op;result_tag<=request_tag;state<=VALIDATE;
                r[0]<=in0;r[1]<=in1;r[2]<=in2;r[3]<=in3;r[4]<=in4;r[5]<=in5;r[6]<=in6;r[7]<=in7;
                r[8]<=in8;r[9]<=in9;r[10]<=in10;r[11]<=in11;r[12]<=in12;r[13]<=in13;r[14]<=in14;r[15]<=in15;
            end else if(busy) case(state)
                VALIDATE: begin
                    if(req_op!=1 && req_op!=2) complete(1);
                    else if(req_op==2 && (r[2]!=0 || r[3]!=0 || r[4]!=0 || r[5]!=0 || r[6]!=0 || r[7]!=0 || r[8]!=0)) complete(2);
                    else if($unsigned(r[11])<1 || $unsigned(r[11])>65535) complete(3);
                    else if(r[12]>r[13] || r[14]>r[15]) complete(4);
                    else if(req_op==2) begin
                        pan_angle_q32<={{16{r[0][31]}},r[0]}<<<10;
                        tilt_angle_q32<={{16{r[1][31]}},r[1]}<<<10;
                        forward_q28<=0;ray_q28<=0;state<=LIMIT_CHECK;
                    end else state<=RELATIVE;
                end
                RELATIVE: begin
                    x<=$signed({r[0][31],r[0]})-$signed({r[3][31],r[3]});
                    y<=$signed({r[1][31],r[1]})-$signed({r[4][31],r[4]});
                    z<=$signed({r[2][31],r[2]})-$signed({r[5][31],r[5]});state<=ZCHECK;
                end
                ZCHECK: if(z<=0) complete(5);else begin sq_index<=0;state<=SQ_LOAD;end
                SQ_LOAD: begin
                    case(sq_index)
                        0:sq_operand<=x;1:sq_operand<=z;2:sq_operand<={r[6][31],r[6]};
                        3:sq_operand<=y;4:sq_operand<={r[7][31],r[7]};
                    endcase
                    state<=SQ_MULT;
                end
                SQ_MULT: begin sq_product<=sq_operand*sq_operand;state<=SQ_SAVE;end
                SQ_SAVE: begin
                    squares[sq_index]<=sq_product;
                    if(sq_index==4) state<=H_COMPUTE;
                    else begin sq_index<=sq_index+1'b1;state<=SQ_LOAD;end
                end
                H_COMPUTE: begin
                    h_rad<=$signed({6'd0,squares[0]})+$signed({6'd0,squares[1]})-$signed({6'd0,squares[2]});state<=H_CHECK;
                end
                H_CHECK: if(h_rad<=0) complete(6);else begin
                    v_rad<=h_rad+$signed({6'd0,squares[3]})-$signed({6'd0,squares[4]});state<=V_CHECK;
                end
                V_CHECK: if(v_rad<=0) complete(7);else begin root_kind<=0;state<=ROOT_LOAD;end
                ROOT_LOAD: begin
                    sqrt_input<=root_kind ? {v_rad,8'd0} : {h_rad,8'd0};sqrt_start<=1;state<=ROOT_WAIT;
                end
                ROOT_WAIT: if(sqrt_done) begin
                    if(!root_kind) begin forward_q28<=sqrt_result;root_kind<=1;state<=ROOT_LOAD;end
                    else begin
                        s_q28<=sqrt_result;
                        ray_q28<=$signed({4'd0,sqrt_result})-($signed({{12{r[8][31]}},r[8]})<<<4);
                        state<=RAY_CHECK;
                    end
                end
                RAY_CHECK: if(ray_q28<=0) complete(8);else begin cord_job<=0;state<=CORD_LOAD;end
                CORD_LOAD: begin
                    case(cord_job)
                        0:begin cord_x<=$signed({{47{z[32]}},z})<<<32;cord_y<=$signed({{47{x[32]}},x})<<<32;end
                        1:begin cord_x<={40'd0,forward_q28}<<28;cord_y<=$signed({{48{r[6][31]}},r[6]})<<<32;end
                        2:begin cord_x<={40'd0,forward_q28}<<28;cord_y<=$signed({{47{y[32]}},y})<<<32;end
                        3:begin cord_x<={40'd0,s_q28}<<28;cord_y<=$signed({{48{r[7][31]}},r[7]})<<<32;end
                    endcase
                    cord_start<=1;state<=CORD_WAIT;
                end
                CORD_WAIT: if(cord_done) begin
                    if(cord_job==3) begin
                        pan_angle_q32<=cangles[0]-cangles[1];tilt_angle_q32<=cangles[2]-cord_result;state<=LIMIT_CHECK;
                    end else begin cangles[cord_job]<=cord_result;cord_job<=cord_job+1'b1;state<=CORD_LOAD;end
                end
                LIMIT_CHECK: begin
                    if(pan_angle_q32<($signed({{16{r[12][31]}},r[12]})<<<10) || pan_angle_q32>($signed({{16{r[13][31]}},r[13]})<<<10) ||
                       tilt_angle_q32<($signed({{16{r[14][31]}},r[14]})<<<10) || tilt_angle_q32>($signed({{16{r[15][31]}},r[15]})<<<10)) complete(9);
                    else begin div_job<=0;state<=JOB_LOAD;end
                end
                JOB_LOAD: begin
                    case(div_job)
                        0:begin job_base<={{32{r[12][31]}},r[12]};job_den<=64'd37748736;round_mode<=1;end
                        1:begin job_base<={{32{r[13][31]}},r[13]};job_den<=64'd37748736;round_mode<=2;end
                        2:begin job_base<={{32{r[14][31]}},r[14]};job_den<=64'd37748736;round_mode<=1;end
                        3:begin job_base<={{32{r[15][31]}},r[15]};job_den<=64'd37748736;round_mode<=2;end
                        4:begin job_base<={{16{pan_angle_q32[47]}},pan_angle_q32};job_den<=64'd38654705664;round_mode<=0;end
                        5:begin job_base<={{16{tilt_angle_q32[47]}},tilt_angle_q32};job_den<=64'd38654705664;round_mode<=0;end
                        6:begin job_base<={{32{target_pan[31]}},target_pan};job_den<=160;round_mode<=0;end
                        7:begin job_base<={{32{target_tilt[31]}},target_tilt};job_den<=160;round_mode<=0;end
                        8:begin job_base<={31'd0,abs_delta_pan};job_den<=6400;round_mode<=3;end
                        9:begin job_base<={31'd0,abs_delta_tilt};job_den<=6400;round_mode<=3;end
                    endcase
                    state<=JOB_MULT;
                end
                JOB_MULT: begin
                    if(div_job<6) job_product<=(job_base<<<7)+(job_base<<<5);
                    else if(div_job<8) job_product<=((job_base<<<3)+job_base)<<<22;
                    else job_product<=job_base;
                    state<=JOB_ABS;
                end
                JOB_ABS: begin job_negative<=job_product<0;job_num<=job_product<0 ? -job_product : job_product;state<=DIV_LAUNCH;end
                DIV_LAUNCH: begin divide_start<=1;state<=DIV_WAIT;end
                DIV_WAIT: if(divide_done) state<=DIV_ROUND;
                DIV_ROUND: begin
                    case(round_mode)
                        0:round_increment<=twice_remainder>{1'b0,job_den} || (twice_remainder=={1'b0,job_den} && divide_quotient[0]);
                        1:round_increment<=!job_negative && divide_remainder!=0;
                        2:round_increment<=job_negative && divide_remainder!=0;
                        3:round_increment<=0;
                    endcase
                    state<=DIV_SIGN;
                end
                DIV_SIGN: begin rounded_magnitude<=divide_quotient+round_increment;state<=JOB_SAVE;end
                JOB_SAVE: begin
                    case(div_job)
                        0:lower_pan<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        1:upper_pan<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        2:lower_tilt<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        3:upper_tilt<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        4:rounded_pan<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        5:rounded_tilt<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        6:estimate_pan<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        7:estimate_tilt<=job_negative ? -rounded_magnitude[31:0] : rounded_magnitude[31:0];
                        8:begin pan_rev<=divide_quotient[31:0];pan_rem<=divide_remainder[31:0];end
                        9:begin tilt_rev<=divide_quotient[31:0];tilt_rem<=divide_remainder[31:0];end
                    endcase
                    if(div_job==3) state<=INTERVAL;
                    else if(div_job==5) state<=CLAMP;
                    else if(div_job==7) state<=DELTA;
                    else if(div_job==9) state<=FINISH;
                    else begin div_job<=div_job+1'b1;state<=JOB_LOAD;end
                end
                INTERVAL: if(lower_pan>upper_pan || lower_tilt>upper_tilt) complete(10);
                    else begin div_job<=4;state<=JOB_LOAD;end
                CLAMP: begin
                    target_pan<=rounded_pan<lower_pan ? lower_pan : rounded_pan>upper_pan ? upper_pan : rounded_pan;
                    target_tilt<=rounded_tilt<lower_tilt ? lower_tilt : rounded_tilt>upper_tilt ? upper_tilt : rounded_tilt;
                    div_job<=6;state<=JOB_LOAD;
                end
                DELTA: begin
                    delta_pan<=$signed({target_pan[31],target_pan})-$signed({r[9][31],r[9]});
                    delta_tilt<=$signed({target_tilt[31],target_tilt})-$signed({r[10][31],r[10]});state<=DELTA_ABS;
                end
                DELTA_ABS: begin abs_delta_pan<=delta_pan<0 ? -delta_pan : delta_pan;abs_delta_tilt<=delta_tilt<0 ? -delta_tilt : delta_tilt;state<=PROTOCOL;end
                PROTOCOL: if(abs_delta_pan>33'd1638399 || abs_delta_tilt>33'd1638399) complete(11);
                    else begin div_job<=8;state<=JOB_LOAD;end
                FINISH: begin
                    out0<=angle_to_q22(pan_angle_q32);out1<=angle_to_q22(tilt_angle_q32);
                    out2<=forward_q28>>8;out3<=ray_q28>>>8;
                    out4<=target_pan;out5<=target_tilt;out6<=delta_pan[31:0];out7<=delta_tilt[31:0];
                    out8<=delta_pan==0 ? 0 : delta_pan>0 ? 1 : 2;
                    out9<=delta_tilt==0 ? 0 : delta_tilt>0 ? 2 : 1;
                    out10<=pan_rev;out11<=pan_rem;out12<=tilt_rev;out13<=tilt_rem;
                    out14<=estimate_pan;out15<=estimate_tilt;complete(0);
                end
                default: complete(1);
            endcase
        end
    end
endmodule
