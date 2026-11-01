`timescale 1ns / 1ps
// Frozen stage7 ABI. Independent AW/W capture and stable B/R backpressure.
module gimbal_axi #(
    parameter integer C_S00_AXI_DATA_WIDTH=32,C_S00_AXI_ADDR_WIDTH=8
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
    output reg s00_axi_rvalid,input wire s00_axi_rready
);
    localparam [1:0] OKAY=0,SLVERR=2;
    reg aw_pending,w_pending;
    reg [7:0] aw_addr;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg [31:0] cfg_op,cfg_tag,cfg_in [0:15];
    reg core_start,core_ack,core_reset,rejected;
    wire core_busy,core_done,core_error;
    wire [31:0] outs [0:15];
    wire [31:0] status,result_tag,completion_count;
    wire command_pending=core_start || core_ack || core_reset;
    assign s00_axi_awready=!aw_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_wready=!w_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_arready=!s00_axi_rvalid;
    integer i;
    function [31:0] merge_bytes;
        input [31:0] previous,update;input [3:0] strobe;integer lane;
        begin
            merge_bytes=previous;
            for(lane=0;lane<4;lane=lane+1) if(strobe[lane]) merge_bytes[lane*8+:8]=update[lane*8+:8];
        end
    endfunction
    gimbal_core core(
        .clk(s00_axi_aclk),.rst_n(s00_axi_aresetn),.start(core_start),.ack_done(core_ack),.reset_state(core_reset),
        .op(cfg_op),.request_tag(cfg_tag),
        .in0(cfg_in[0]),.in1(cfg_in[1]),.in2(cfg_in[2]),.in3(cfg_in[3]),.in4(cfg_in[4]),.in5(cfg_in[5]),.in6(cfg_in[6]),.in7(cfg_in[7]),
        .in8(cfg_in[8]),.in9(cfg_in[9]),.in10(cfg_in[10]),.in11(cfg_in[11]),.in12(cfg_in[12]),.in13(cfg_in[13]),.in14(cfg_in[14]),.in15(cfg_in[15]),
        .busy(core_busy),.done(core_done),.error(core_error),
        .out0(outs[0]),.out1(outs[1]),.out2(outs[2]),.out3(outs[3]),.out4(outs[4]),.out5(outs[5]),.out6(outs[6]),.out7(outs[7]),
        .out8(outs[8]),.out9(outs[9]),.out10(outs[10]),.out11(outs[11]),.out12(outs[12]),.out13(outs[13]),.out14(outs[14]),.out15(outs[15]),
        .status(status),.result_tag(result_tag),.completion_count(completion_count)
    );
    always @(posedge s00_axi_aclk) begin
        if(!s00_axi_aresetn) begin
            aw_pending<=0;w_pending<=0;aw_addr<=0;w_data<=0;w_strobe<=0;
            s00_axi_bresp<=OKAY;s00_axi_bvalid<=0;s00_axi_rdata<=0;s00_axi_rresp<=OKAY;s00_axi_rvalid<=0;
            cfg_op<=0;cfg_tag<=0;for(i=0;i<16;i=i+1) cfg_in[i]<=0;
            core_start<=0;core_ack<=0;core_reset<=0;rejected<=0;
        end else begin
            core_start<=0;core_ack<=0;core_reset<=0;
            if(s00_axi_bvalid && s00_axi_bready) s00_axi_bvalid<=0;
            if(s00_axi_rvalid && s00_axi_rready) s00_axi_rvalid<=0;
            if(s00_axi_awvalid && s00_axi_awready) begin aw_addr<=s00_axi_awaddr;aw_pending<=1;end
            if(s00_axi_wvalid && s00_axi_wready) begin w_data<=s00_axi_wdata;w_strobe<=s00_axi_wstrb;w_pending<=1;end
            if(aw_pending && w_pending && !s00_axi_bvalid && !command_pending) begin
                aw_pending<=0;w_pending<=0;s00_axi_bvalid<=1;s00_axi_bresp<=OKAY;
                if(aw_addr[1:0]!=0) s00_axi_bresp<=SLVERR;
                else if(aw_addr==8'h00) begin
                    if(w_strobe!=4'hf) s00_axi_bresp<=SLVERR;
                    else case(w_data)
                        1:if(core_busy || core_done) begin s00_axi_bresp<=SLVERR;rejected<=1;end else core_start<=1;
                        2:if(core_done && !core_busy) begin core_ack<=1;rejected<=0;end else s00_axi_bresp<=SLVERR;
                        4:begin core_reset<=1;rejected<=0;end
                        default:s00_axi_bresp<=SLVERR;
                    endcase
                end else if(aw_addr==8'h10 || aw_addr==8'h54 || (aw_addr>=8'h14 && aw_addr<=8'h50)) begin
                    if(core_busy) begin s00_axi_bresp<=SLVERR;rejected<=1;end
                    else if(aw_addr==8'h10) cfg_op<=merge_bytes(cfg_op,w_data,w_strobe);
                    else if(aw_addr==8'h54) cfg_tag<=merge_bytes(cfg_tag,w_data,w_strobe);
                    else cfg_in[(aw_addr-8'h14)>>2]<=merge_bytes(cfg_in[(aw_addr-8'h14)>>2],w_data,w_strobe);
                end else s00_axi_bresp<=SLVERR;
            end
            if(s00_axi_arvalid && s00_axi_arready) begin
                s00_axi_rvalid<=1;s00_axi_rresp<=OKAY;s00_axi_rdata<=0;
                if(s00_axi_araddr[1:0]!=0) s00_axi_rresp<=SLVERR;
                else if(s00_axi_araddr>=8'h14 && s00_axi_araddr<=8'h50) s00_axi_rdata<=cfg_in[(s00_axi_araddr-8'h14)>>2];
                else if(s00_axi_araddr>=8'h60 && s00_axi_araddr<=8'h9c) s00_axi_rdata<=outs[(s00_axi_araddr-8'h60)>>2];
                else case(s00_axi_araddr)
                    8'h00:s00_axi_rdata<=0;
                    8'h04:s00_axi_rdata<={28'd0,rejected,core_error,core_done,(core_busy||core_start)};
                    8'h08:s00_axi_rdata<=32'h474d4231;
                    8'h0c:s00_axi_rdata<=32'h00010000;
                    8'h10:s00_axi_rdata<=cfg_op;
                    8'h54:s00_axi_rdata<=cfg_tag;
                    8'ha0:s00_axi_rdata<=status;
                    8'ha4:s00_axi_rdata<=result_tag;
                    8'ha8:s00_axi_rdata<=completion_count;
                    default:s00_axi_rresp<=SLVERR;
                endcase
            end
        end
    end
`ifndef SYNTHESIS
    initial if(C_S00_AXI_DATA_WIDTH!=32 || C_S00_AXI_ADDR_WIDTH!=8) begin
        $error("gimbal_axi fixed ABI requires DATA_WIDTH=32 ADDR_WIDTH=8");$finish;
    end
`endif
endmodule
