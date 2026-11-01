`timescale 1ns / 1ps
// Independent AW/W capture and stable B/R responses under arbitrary backpressure.
module interaction_axi #(
    parameter integer C_S00_AXI_DATA_WIDTH=32,
    parameter integer C_S00_AXI_ADDR_WIDTH=8
) (
    input wire s00_axi_aclk, input wire s00_axi_aresetn,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_awaddr,
    input wire [2:0] s00_axi_awprot,
    input wire s00_axi_awvalid, output wire s00_axi_awready,
    input wire [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_wdata,
    input wire [(C_S00_AXI_DATA_WIDTH/8)-1:0] s00_axi_wstrb,
    input wire s00_axi_wvalid, output wire s00_axi_wready,
    output reg [1:0] s00_axi_bresp, output reg s00_axi_bvalid,
    input wire s00_axi_bready,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_araddr,
    input wire [2:0] s00_axi_arprot,
    input wire s00_axi_arvalid, output wire s00_axi_arready,
    output reg [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_rdata,
    output reg [1:0] s00_axi_rresp, output reg s00_axi_rvalid,
    input wire s00_axi_rready
);
    localparam [1:0] OKAY=2'b00,SLVERR=2'b10;
    reg aw_pending,w_pending;
    reg [7:0] aw_addr;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg [31:0] cfg_op,cfg_a,cfg_b,cfg_c,cfg_d,cfg_e,cfg_f,cfg_g,cfg_h,cfg_tag;
    reg core_start,core_ack,core_reset,rejected;
    wire core_busy,core_done,core_error;
    wire [31:0] out0,out1,out2,out3,out4,out5,out6,out7,status,result_tag,completion_count;
    wire command_pending=core_start || core_ack || core_reset;
    assign s00_axi_awready=!aw_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_wready=!w_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_arready=!s00_axi_rvalid;
    function [31:0] merge_bytes;
        input [31:0] previous,update;
        input [3:0] strobe;
        integer lane;
        begin
            merge_bytes=previous;
            for(lane=0;lane<4;lane=lane+1)
                if(strobe[lane]) merge_bytes[lane*8+:8]=update[lane*8+:8];
        end
    endfunction
    interaction_core core(
        .clk(s00_axi_aclk),.rst_n(s00_axi_aresetn),.start(core_start),
        .ack_done(core_ack),.reset_state(core_reset),
        .op(cfg_op),.a(cfg_a),.b(cfg_b),.c(cfg_c),.d(cfg_d),.e(cfg_e),
        .f(cfg_f),.g(cfg_g),.h(cfg_h),.request_tag(cfg_tag),
        .busy(core_busy),.done(core_done),.error(core_error),
        .out0(out0),.out1(out1),.out2(out2),.out3(out3),.out4(out4),.out5(out5),.out6(out6),.out7(out7),
        .status(status),.result_tag(result_tag),.completion_count(completion_count)
    );
    always @(posedge s00_axi_aclk) begin
        if(!s00_axi_aresetn) begin
            aw_pending<=0;w_pending<=0;aw_addr<=0;w_data<=0;w_strobe<=0;
            s00_axi_bresp<=OKAY;s00_axi_bvalid<=0;s00_axi_rdata<=0;s00_axi_rresp<=OKAY;s00_axi_rvalid<=0;
            cfg_op<=0;cfg_a<=0;cfg_b<=0;cfg_c<=0;cfg_d<=0;cfg_e<=0;cfg_f<=0;cfg_g<=0;cfg_h<=0;cfg_tag<=0;
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
                else case(aw_addr)
                    8'h00: begin
                        if(w_strobe!=4'hf) s00_axi_bresp<=SLVERR;
                        else case(w_data)
                            32'd1: if(core_busy || core_done) begin s00_axi_bresp<=SLVERR;rejected<=1;end
                                   else core_start<=1;
                            32'd2: if(core_done && !core_busy) begin core_ack<=1;rejected<=0;end
                                   else s00_axi_bresp<=SLVERR;
                            32'd4: begin core_reset<=1;rejected<=0;end
                            default: s00_axi_bresp<=SLVERR;
                        endcase
                    end
                    8'h10,8'h14,8'h18,8'h1c,8'h20,8'h24,8'h28,8'h2c,8'h30,8'h3c: begin
                        if(core_busy) begin s00_axi_bresp<=SLVERR;rejected<=1;end
                        else case(aw_addr)
                            8'h10:cfg_op<=merge_bytes(cfg_op,w_data,w_strobe);
                            8'h14:cfg_a<=merge_bytes(cfg_a,w_data,w_strobe);
                            8'h18:cfg_b<=merge_bytes(cfg_b,w_data,w_strobe);
                            8'h1c:cfg_c<=merge_bytes(cfg_c,w_data,w_strobe);
                            8'h20:cfg_d<=merge_bytes(cfg_d,w_data,w_strobe);
                            8'h24:cfg_e<=merge_bytes(cfg_e,w_data,w_strobe);
                            8'h28:cfg_f<=merge_bytes(cfg_f,w_data,w_strobe);
                            8'h2c:cfg_g<=merge_bytes(cfg_g,w_data,w_strobe);
                            8'h30:cfg_h<=merge_bytes(cfg_h,w_data,w_strobe);
                            8'h3c:cfg_tag<=merge_bytes(cfg_tag,w_data,w_strobe);
                        endcase
                    end
                    default:s00_axi_bresp<=SLVERR;
                endcase
            end
            if(s00_axi_arvalid && s00_axi_arready) begin
                s00_axi_rvalid<=1;s00_axi_rresp<=OKAY;s00_axi_rdata<=0;
                if(s00_axi_araddr[1:0]!=0) s00_axi_rresp<=SLVERR;
                else case(s00_axi_araddr)
                    8'h00:s00_axi_rdata<=0;
                    8'h04:s00_axi_rdata<={28'd0,rejected,core_error,core_done,(core_busy||core_start)};
                    8'h08:s00_axi_rdata<=32'h47494e32;
                    8'h0c:s00_axi_rdata<=32'h00020000;
                    8'h10:s00_axi_rdata<=cfg_op;
                    8'h14:s00_axi_rdata<=cfg_a;
                    8'h18:s00_axi_rdata<=cfg_b;
                    8'h1c:s00_axi_rdata<=cfg_c;
                    8'h20:s00_axi_rdata<=cfg_d;
                    8'h24:s00_axi_rdata<=cfg_e;
                    8'h28:s00_axi_rdata<=cfg_f;
                    8'h2c:s00_axi_rdata<=cfg_g;
                    8'h30:s00_axi_rdata<=cfg_h;
                    8'h3c:s00_axi_rdata<=cfg_tag;
                    8'h40:s00_axi_rdata<=out0;
                    8'h44:s00_axi_rdata<=out1;
                    8'h48:s00_axi_rdata<=out2;
                    8'h4c:s00_axi_rdata<=out3;
                    8'h50:s00_axi_rdata<=out4;
                    8'h54:s00_axi_rdata<=out5;
                    8'h58:s00_axi_rdata<=out6;
                    8'h5c:s00_axi_rdata<=out7;
                    8'h60:s00_axi_rdata<=status;
                    8'h64:s00_axi_rdata<=result_tag;
                    8'h68:s00_axi_rdata<=completion_count;
                    default:s00_axi_rresp<=SLVERR;
                endcase
            end
        end
    end
`ifndef SYNTHESIS
    initial begin
        if(C_S00_AXI_DATA_WIDTH!=32 || C_S00_AXI_ADDR_WIDTH!=8) begin
            $error("interaction_axi fixed ABI requires DATA_WIDTH=32 ADDR_WIDTH=8");$finish;
        end
    end
`endif
endmodule
