`timescale 1ns / 1ps
// Isolated stage8 ABI: independent AW/W caching, stable stalled B/R responses.
module gimbal_uart_axi #(
    parameter integer C_S00_AXI_DATA_WIDTH=32,C_S00_AXI_ADDR_WIDTH=9
)(
    input wire s00_axi_aclk,s00_axi_aresetn,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_awaddr,input wire [2:0] s00_axi_awprot,
    input wire s00_axi_awvalid,output wire s00_axi_awready,
    input wire [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_wdata,input wire [(C_S00_AXI_DATA_WIDTH/8)-1:0] s00_axi_wstrb,
    input wire s00_axi_wvalid,output wire s00_axi_wready,
    output reg [1:0] s00_axi_bresp,output reg s00_axi_bvalid,input wire s00_axi_bready,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_araddr,input wire [2:0] s00_axi_arprot,
    input wire s00_axi_arvalid,output wire s00_axi_arready,
    output reg [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_rdata,output reg [1:0] s00_axi_rresp,
    output reg s00_axi_rvalid,input wire s00_axi_rready,
    input wire uart_rx,output wire uart_tx
);
    localparam [1:0] OKAY=0,SLVERR=2;
    reg aw_pending,w_pending;
    reg [8:0] aw_addr;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg [31:0] cfg_op,cfg_tag,cfg_in [0:15],interface_profile,test_reply_word;
    reg mode_test,mode_changed,rejected,inject_start;
    reg [7:0] command;
    reg [31:0] control_diagnostic;
    wire engine_busy,engine_done,engine_error,armed,position_valid,fault,injection_busy;
    wire tx_bit,external_oe;
    wire [31:0] engine_read_data;
    wire engine_read_valid;
    wire [511:0] cfg_inputs;
    genvar g;
    generate for(g=0;g<16;g=g+1) begin:CFG_WORDS
        assign cfg_inputs[g*32+:32]=cfg_in[g];
    end endgenerate
    wire command_pending=command!=0 || inject_start || mode_changed || control_diagnostic!=0;
    assign s00_axi_awready=!aw_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_wready=!w_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_arready=!s00_axi_rvalid;
    wire transaction_busy=engine_busy || command==1 || command==64 || ((command==16 || command==32) && armed);
    wire signed [31:0] seed_pan=cfg_in[9],seed_tilt=cfg_in[10];
    wire seed_in_range=seed_pan>=-9102 && seed_pan<=9102 && seed_tilt>=-9102 && seed_tilt<=9102;
    integer i;
    function [31:0] merge_bytes;
        input [31:0] previous,update;input [3:0] strobe;integer lane;
        begin
            merge_bytes=previous;
            for(lane=0;lane<4;lane=lane+1) if(strobe[lane]) merge_bytes[lane*8+:8]=update[lane*8+:8];
        end
    endfunction
    task reject_control;
        input [31:0] code;
        begin s00_axi_bresp<=SLVERR;rejected<=1;control_diagnostic<=code;end
    endtask
    gimbal_uart_engine engine(
        .clk(s00_axi_aclk),.rst_n(s00_axi_aresetn),.control(command),.control_diagnostic(control_diagnostic),
        .mode_test(mode_test),.mode_changed(mode_changed),.interface_profile(interface_profile),
        .cfg_op(cfg_op),.cfg_tag(cfg_tag),.cfg_inputs(cfg_inputs),
        .inject_start(inject_start),.inject_word(test_reply_word),.external_rx(uart_rx),
        .read_addr(s00_axi_araddr),.read_data(engine_read_data),.read_valid(engine_read_valid),
        .tx_bit(tx_bit),.external_oe(external_oe),.busy(engine_busy),.done(engine_done),.error(engine_error),
        .armed(armed),.position_valid(position_valid),.fault(fault),.injection_busy(injection_busy)
    );
    // The pad is explicitly disabled from reset and in EVERY internal test state.
    OBUFT tx_output_buffer(.I(tx_bit),.T(!external_oe),.O(uart_tx));
    always @(posedge s00_axi_aclk) begin
        if(!s00_axi_aresetn) begin
            aw_pending<=0;w_pending<=0;aw_addr<=0;w_data<=0;w_strobe<=0;
            s00_axi_bresp<=OKAY;s00_axi_bvalid<=0;s00_axi_rdata<=0;s00_axi_rresp<=OKAY;s00_axi_rvalid<=0;
            cfg_op<=0;cfg_tag<=0;for(i=0;i<16;i=i+1) cfg_in[i]<=0;
            interface_profile<=0;test_reply_word<=0;mode_test<=1;mode_changed<=0;rejected<=0;
            inject_start<=0;command<=0;control_diagnostic<=0;
        end else begin
            command<=0;mode_changed<=0;inject_start<=0;control_diagnostic<=0;
            if(s00_axi_bvalid && s00_axi_bready) s00_axi_bvalid<=0;
            if(s00_axi_rvalid && s00_axi_rready) s00_axi_rvalid<=0;
            if(s00_axi_awvalid && s00_axi_awready) begin aw_addr<=s00_axi_awaddr;aw_pending<=1;end
            if(s00_axi_wvalid && s00_axi_wready) begin w_data<=s00_axi_wdata;w_strobe<=s00_axi_wstrb;w_pending<=1;end
            if(aw_pending && w_pending && !s00_axi_bvalid && !command_pending) begin
                aw_pending<=0;w_pending<=0;s00_axi_bvalid<=1;s00_axi_bresp<=OKAY;
                if(aw_addr[1:0]!=0) s00_axi_bresp<=SLVERR;
                else if(aw_addr==9'h000) begin
                    if(w_strobe!=4'hf) reject_control(16);
                    else case(w_data)
                        1:if(engine_busy || engine_done) reject_control(16);else command<=1;
                        2:if(engine_done && !engine_busy) begin command<=2;rejected<=0;end else reject_control(16);
                        4:if(!armed && !engine_busy) begin command<=4;rejected<=0;end else reject_control(16);
                        8:if(engine_busy || engine_done) reject_control(16);
                            else if(!mode_test && interface_profile!=32'h334c5454) reject_control(19);
                            else if(!position_valid || fault) reject_control(16);else command<=8;
                        16:command<=16;
                        32:command<=32;
                        64:if(armed || engine_busy || engine_done || !seed_in_range) reject_control(16);else command<=64;
                        128:if(armed) command<=128;else reject_control(16);
                        default:reject_control(16);
                    endcase
                end else if(aw_addr==9'h010 || aw_addr==9'h054 || (aw_addr>=9'h014 && aw_addr<=9'h050)) begin
                    if(engine_busy) begin s00_axi_bresp<=SLVERR;rejected<=1;end
                    else if(aw_addr==9'h010) cfg_op<=merge_bytes(cfg_op,w_data,w_strobe);
                    else if(aw_addr==9'h054) cfg_tag<=merge_bytes(cfg_tag,w_data,w_strobe);
                    else cfg_in[(aw_addr-9'h014)>>2]<=merge_bytes(cfg_in[(aw_addr-9'h014)>>2],w_data,w_strobe);
                end else if(aw_addr==9'h0b0) begin
                    if(armed || engine_busy || merge_bytes({31'd0,mode_test},w_data,w_strobe)>1) reject_control(16);
                    else if(merge_bytes({31'd0,mode_test},w_data,w_strobe)!={31'd0,mode_test}) begin
                        mode_test<=w_data[0];mode_changed<=1;interface_profile<=0;
                    end
                end else if(aw_addr==9'h0b8) begin
                    if(armed || engine_busy) reject_control(16);
                    else interface_profile<=merge_bytes(interface_profile,w_data,w_strobe);
                end else if(aw_addr==9'h0f8) begin
                    if(!mode_test || engine_busy) reject_control(16);
                    else test_reply_word<=merge_bytes(test_reply_word,w_data,w_strobe);
                end else if(aw_addr==9'h0fc) begin
                    if(!mode_test || engine_busy || w_strobe!=4'hf || w_data!=1) reject_control(16);
                    else inject_start<=1;
                end else s00_axi_bresp<=SLVERR;
            end
            if(s00_axi_arvalid && s00_axi_arready) begin
                s00_axi_rvalid<=1;s00_axi_rresp<=OKAY;s00_axi_rdata<=0;
                if(s00_axi_araddr[1:0]!=0) s00_axi_rresp<=SLVERR;
                else if(s00_axi_araddr>=9'h014 && s00_axi_araddr<=9'h050) s00_axi_rdata<=cfg_in[(s00_axi_araddr-9'h014)>>2];
                else case(s00_axi_araddr)
                    9'h000:s00_axi_rdata<=0;
                    9'h004:s00_axi_rdata<={28'd0,rejected,engine_error,engine_done,transaction_busy};
                    9'h008:s00_axi_rdata<=32'h47554131;
                    9'h00c:s00_axi_rdata<=32'h00010000;
                    9'h010:s00_axi_rdata<=cfg_op;
                    9'h054:s00_axi_rdata<=cfg_tag;
                    9'h0b0:s00_axi_rdata<={31'd0,mode_test};
                    9'h0b8:s00_axi_rdata<=interface_profile;
                    9'h0f8:s00_axi_rdata<=test_reply_word;
                    9'h0fc:s00_axi_rdata<=0;
                    default:if(engine_read_valid) s00_axi_rdata<=engine_read_data;else s00_axi_rresp<=SLVERR;
                endcase
            end
        end
    end
`ifndef SYNTHESIS
    initial if(C_S00_AXI_DATA_WIDTH!=32 || C_S00_AXI_ADDR_WIDTH!=9) begin
        $error("gimbal_uart_axi fixed ABI requires DATA_WIDTH=32 ADDR_WIDTH=9");$finish;
    end
`endif
endmodule
