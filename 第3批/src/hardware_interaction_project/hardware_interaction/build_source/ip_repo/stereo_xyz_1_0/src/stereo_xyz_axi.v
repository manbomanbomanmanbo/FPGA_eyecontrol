`timescale 1ns / 1ps
// Independent AXI4-Lite peripheral: fixed 32-bit data, 7-bit byte address.
module stereo_xyz_axi #(
    parameter integer C_S00_AXI_DATA_WIDTH = 32,
    parameter integer C_S00_AXI_ADDR_WIDTH = 7
) (
    input wire s00_axi_aclk,
    input wire s00_axi_aresetn,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_awaddr,
    input wire [2:0] s00_axi_awprot,
    input wire s00_axi_awvalid,
    output wire s00_axi_awready,
    input wire [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_wdata,
    input wire [(C_S00_AXI_DATA_WIDTH/8)-1:0] s00_axi_wstrb,
    input wire s00_axi_wvalid,
    output wire s00_axi_wready,
    output reg [1:0] s00_axi_bresp,
    output reg s00_axi_bvalid,
    input wire s00_axi_bready,
    input wire [C_S00_AXI_ADDR_WIDTH-1:0] s00_axi_araddr,
    input wire [2:0] s00_axi_arprot,
    input wire s00_axi_arvalid,
    output wire s00_axi_arready,
    output reg [C_S00_AXI_DATA_WIDTH-1:0] s00_axi_rdata,
    output reg [1:0] s00_axi_rresp,
    output reg s00_axi_rvalid,
    input wire s00_axi_rready
);
    localparam [1:0] OKAY = 2'b00, SLVERR = 2'b10;
    reg aw_pending, w_pending;
    reg [6:0] aw_addr;
    reg [31:0] w_data;
    reg [3:0] w_strobe;
    reg signed [31:0] cfg_u, cfg_v, cfg_d, cfg_fx, cfg_fy, cfg_cx, cfg_cy;
    reg signed [31:0] cfg_b, cfg_z_min, cfg_z_max;
    reg [31:0] cfg_width, cfg_height, cfg_valid, cfg_tag, scratch;
    reg core_start, core_ack, core_abort, start_rejected;
    wire core_busy, core_done, core_error;
    wire signed [31:0] result_x, result_y, result_z;
    wire [31:0] result_status, result_tag, completion_count;

    // Keep a command reservation until the core has consumed the pulse.
    // This closes the AXI/core one-clock handoff window for START/ACK/ABORT.
    wire command_pending = core_start || core_ack || core_abort;
    assign s00_axi_awready = !aw_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_wready = !w_pending && !s00_axi_bvalid && !command_pending;
    assign s00_axi_arready = !s00_axi_rvalid;
    wire [31:0] byte_mask = {{8{w_strobe[3]}}, {8{w_strobe[2]}},
                            {8{w_strobe[1]}}, {8{w_strobe[0]}}};
    wire [31:0] command = w_data & byte_mask;
    wire [31:0] valid_merged = (cfg_valid & ~byte_mask) | (w_data & byte_mask);
    wire [31:0] state_word = {28'd0, start_rejected, core_error,
                              core_done, (core_busy || core_start)};

    function [31:0] merge_bytes;
        input [31:0] previous;
        input [31:0] update;
        input [3:0] strobe;
        integer lane;
        begin
            merge_bytes = previous;
            for (lane = 0; lane < 4; lane = lane + 1)
                if (strobe[lane])
                    merge_bytes[lane*8 +: 8] = update[lane*8 +: 8];
        end
    endfunction

    stereo_xyz_core core (
        .clk(s00_axi_aclk), .rst_n(s00_axi_aresetn),
        .start(core_start), .ack(core_ack), .abort(core_abort),
        .u(cfg_u), .v(cfg_v), .d(cfg_d), .fx(cfg_fx), .fy(cfg_fy),
        .cx(cfg_cx), .cy(cfg_cy), .baseline_m(cfg_b),
        .z_min(cfg_z_min), .z_max(cfg_z_max),
        .width(cfg_width), .height(cfg_height),
        .input_valid(cfg_valid[0]), .request_tag(cfg_tag),
        .busy(core_busy), .done(core_done), .error(core_error),
        .x(result_x), .y(result_y), .z(result_z),
        .status(result_status), .result_tag(result_tag),
        .completion_count(completion_count)
    );

    always @(posedge s00_axi_aclk) begin
        if (!s00_axi_aresetn) begin
            aw_pending <= 0; w_pending <= 0;
            aw_addr <= 0; w_data <= 0; w_strobe <= 0;
            s00_axi_bresp <= OKAY; s00_axi_bvalid <= 0;
            s00_axi_rdata <= 0; s00_axi_rresp <= OKAY; s00_axi_rvalid <= 0;
            cfg_u <= 0; cfg_v <= 0; cfg_d <= 0; cfg_fx <= 0; cfg_fy <= 0;
            cfg_cx <= 0; cfg_cy <= 0; cfg_b <= 0;
            cfg_z_min <= 0; cfg_z_max <= 0;
            cfg_width <= 0; cfg_height <= 0; cfg_valid <= 0; cfg_tag <= 0;
            scratch <= 0; start_rejected <= 0;
            core_start <= 0; core_ack <= 0; core_abort <= 0;
        end else begin
            core_start <= 0; core_ack <= 0; core_abort <= 0;
            if (s00_axi_bvalid && s00_axi_bready)
                s00_axi_bvalid <= 0;
            if (s00_axi_rvalid && s00_axi_rready)
                s00_axi_rvalid <= 0;
            if (s00_axi_awvalid && s00_axi_awready) begin
                aw_addr <= s00_axi_awaddr;
                aw_pending <= 1;
            end
            if (s00_axi_wvalid && s00_axi_wready) begin
                w_data <= s00_axi_wdata;
                w_strobe <= s00_axi_wstrb;
                w_pending <= 1;
            end
            if (aw_pending && w_pending && !s00_axi_bvalid && !command_pending) begin
                aw_pending <= 0;
                w_pending <= 0;
                s00_axi_bvalid <= 1;
                s00_axi_bresp <= OKAY;
                if (aw_addr[1:0] != 0)
                    s00_axi_bresp <= SLVERR;
                else case (aw_addr)
                    7'h00: begin
                        case (command)
                            32'd0: begin end
                            32'd1: if (core_busy || core_done) begin
                                s00_axi_bresp <= SLVERR;
                                start_rejected <= 1;
                            end else core_start <= 1;
                            32'd2: if (core_done && !core_busy)
                                core_ack <= 1;
                            else s00_axi_bresp <= SLVERR;
                            32'd4: core_abort <= 1;
                            32'd8: start_rejected <= 0;
                            default: s00_axi_bresp <= SLVERR;
                        endcase
                    end
                    7'h10, 7'h14, 7'h18, 7'h1c, 7'h20, 7'h24, 7'h28,
                    7'h2c, 7'h30, 7'h34, 7'h38, 7'h3c, 7'h40, 7'h44: begin
                        if (core_busy) begin
                            s00_axi_bresp <= SLVERR;
                            start_rejected <= 1;
                        end else if (aw_addr == 7'h40 && valid_merged > 1)
                            s00_axi_bresp <= SLVERR;
                        else case (aw_addr)
                            7'h10: cfg_u <= merge_bytes(cfg_u, w_data, w_strobe);
                            7'h14: cfg_v <= merge_bytes(cfg_v, w_data, w_strobe);
                            7'h18: cfg_d <= merge_bytes(cfg_d, w_data, w_strobe);
                            7'h1c: cfg_fx <= merge_bytes(cfg_fx, w_data, w_strobe);
                            7'h20: cfg_fy <= merge_bytes(cfg_fy, w_data, w_strobe);
                            7'h24: cfg_cx <= merge_bytes(cfg_cx, w_data, w_strobe);
                            7'h28: cfg_cy <= merge_bytes(cfg_cy, w_data, w_strobe);
                            7'h2c: cfg_b <= merge_bytes(cfg_b, w_data, w_strobe);
                            7'h30: cfg_width <= merge_bytes(cfg_width, w_data, w_strobe);
                            7'h34: cfg_height <= merge_bytes(cfg_height, w_data, w_strobe);
                            7'h38: cfg_z_min <= merge_bytes(cfg_z_min, w_data, w_strobe);
                            7'h3c: cfg_z_max <= merge_bytes(cfg_z_max, w_data, w_strobe);
                            7'h40: cfg_valid <= valid_merged;
                            7'h44: cfg_tag <= merge_bytes(cfg_tag, w_data, w_strobe);
                        endcase
                    end
                    7'h60: scratch <= merge_bytes(scratch, w_data, w_strobe);
                    default: s00_axi_bresp <= SLVERR;
                endcase
            end
            if (s00_axi_arvalid && s00_axi_arready) begin
                s00_axi_rvalid <= 1;
                s00_axi_rresp <= OKAY;
                s00_axi_rdata <= 0;
                if (s00_axi_araddr[1:0] != 0)
                    s00_axi_rresp <= SLVERR;
                else case (s00_axi_araddr)
                    7'h00: s00_axi_rdata <= 0;
                    7'h04: s00_axi_rdata <= state_word;
                    7'h08: s00_axi_rdata <= 32'h53545831;
                    7'h0c: s00_axi_rdata <= 32'h00010000;
                    7'h10: s00_axi_rdata <= cfg_u;
                    7'h14: s00_axi_rdata <= cfg_v;
                    7'h18: s00_axi_rdata <= cfg_d;
                    7'h1c: s00_axi_rdata <= cfg_fx;
                    7'h20: s00_axi_rdata <= cfg_fy;
                    7'h24: s00_axi_rdata <= cfg_cx;
                    7'h28: s00_axi_rdata <= cfg_cy;
                    7'h2c: s00_axi_rdata <= cfg_b;
                    7'h30: s00_axi_rdata <= cfg_width;
                    7'h34: s00_axi_rdata <= cfg_height;
                    7'h38: s00_axi_rdata <= cfg_z_min;
                    7'h3c: s00_axi_rdata <= cfg_z_max;
                    7'h40: s00_axi_rdata <= cfg_valid;
                    7'h44: s00_axi_rdata <= cfg_tag;
                    7'h48: s00_axi_rdata <= result_x;
                    7'h4c: s00_axi_rdata <= result_y;
                    7'h50: s00_axi_rdata <= result_z;
                    7'h54: s00_axi_rdata <= result_status;
                    7'h58: s00_axi_rdata <= result_tag;
                    7'h5c: s00_axi_rdata <= completion_count;
                    7'h60: s00_axi_rdata <= scratch;
                    default: s00_axi_rresp <= SLVERR;
                endcase
            end
        end
    end

    // These parameters describe the fixed ABI, rather than selectable bus widths.
    generate
        if (C_S00_AXI_DATA_WIDTH != 32 || C_S00_AXI_ADDR_WIDTH != 7) begin : invalid_parameters
            initial begin
                $error("stereo_xyz_axi requires DATA_WIDTH=32 and ADDR_WIDTH=7");
                $finish;
            end
        end
    endgenerate
    // ILA_DEBUG_STAGE5_BEGIN: read-only combinational taps
    (* mark_debug = "true", keep = "true" *) wire ila_stereo_start;
    assign ila_stereo_start = core_start;
    (* mark_debug = "true", keep = "true" *) wire ila_stereo_busy;
    assign ila_stereo_busy = core_busy;
    (* mark_debug = "true", keep = "true" *) wire ila_stereo_done;
    assign ila_stereo_done = core_done;
    (* mark_debug = "true", keep = "true" *) wire ila_stereo_error;
    assign ila_stereo_error = core_error;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_u;
    assign ila_stereo_u = cfg_u;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_v;
    assign ila_stereo_v = cfg_v;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_disparity;
    assign ila_stereo_disparity = cfg_d;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_result_x;
    assign ila_stereo_result_x = result_x;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_result_y;
    assign ila_stereo_result_y = result_y;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_result_z;
    assign ila_stereo_result_z = result_z;
    (* mark_debug = "true", keep = "true" *) wire [2:0] ila_stereo_status;
    assign ila_stereo_status = result_status[2:0];
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_stereo_count;
    assign ila_stereo_count = completion_count;
    // ILA_DEBUG_STAGE5_END
endmodule
