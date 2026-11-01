`timescale 1ns / 1ps
// GAZE_AXI_STEP2_V1
// Replacement for the wizard-generated slave, retaining its name and ports.
// 32-bit AXI4-Lite, 128-byte register window, a single clock domain.
// Independent one-entry AW/W buffers; one write response and one read at a time.
// Read data is snapshotted at the AR handshake and held until accepted.
// The original gaze_map kernel is instantiated unchanged below.
// See docs/register_map.md for the software-visible contract.
module gaze_map_axi_slave_lite_v1_0_S00_AXI #(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 7
)(
    input wire S_AXI_ACLK,
    input wire S_AXI_ARESETN,
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    input wire [2:0] S_AXI_AWPROT,
    input wire S_AXI_AWVALID,
    output wire S_AXI_AWREADY,
    input wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_WDATA,
    input wire [(C_S_AXI_DATA_WIDTH/8)-1:0] S_AXI_WSTRB,
    input wire S_AXI_WVALID,
    output wire S_AXI_WREADY,
    output wire [1:0] S_AXI_BRESP,
    output wire S_AXI_BVALID,
    input wire S_AXI_BREADY,
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    input wire [2:0] S_AXI_ARPROT,
    input wire S_AXI_ARVALID,
    output wire S_AXI_ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_RDATA,
    output wire [1:0] S_AXI_RRESP,
    output wire S_AXI_RVALID,
    input wire S_AXI_RREADY
);
    // This implementation deliberately supports only the wizard settings used
    // in this project. Do not customize either width when packaging the IP.
    // synthesis translate_off
    initial begin
        if (C_S_AXI_DATA_WIDTH != 32 || C_S_AXI_ADDR_WIDTH != 7)
            $fatal(1, "gaze_map_axi requires DATA_WIDTH=32 and ADDR_WIDTH=7");
    end
    // synthesis translate_on

    reg aw_pending, w_pending;
    reg [6:0] aw_saved;
    reg [31:0] w_saved;
    reg [3:0] wstrb_saved;
    reg bvalid_r;
    reg [1:0] bresp_r;
    reg rvalid_r;
    reg [31:0] rdata_r;
    reg [1:0] rresp_r;

    // cfg[0..13]: gx,gy,a0,a1,a2,a3,b0,b1,b2,b3,width,height,valid,tag.
    // Staging registers may be written while computing. The kernel snapshots
    // them when its start pulse is sampled, before another write can commit.
    reg [31:0] cfg [0:13];
    reg [31:0] scratch;
    reg start_pulse, soft_reset_pulse;
    reg active_request, done_sticky, reject_sticky;
    reg [31:0] active_tag;
    reg [31:0] result_x, result_y, result_status, result_tag;
    reg [31:0] complete_count;
    wire core_busy, core_done;
    wire [31:0] core_x, core_y, core_status;
    wire core_reset_n = S_AXI_ARESETN && !soft_reset_pulse;
    wire [4:0] write_word = aw_saved[6:2];
    wire [4:0] read_word = S_AXI_ARADDR[6:2];
    wire write_commit = aw_pending && w_pending && !bvalid_r;
    wire [31:0] command_word = w_saved &
        {{8{wstrb_saved[3]}}, {8{wstrb_saved[2]}},
         {8{wstrb_saved[1]}}, {8{wstrb_saved[0]}}};
    wire [31:0] state_word = {28'd0, reject_sticky,
        (done_sticky && (result_status != 32'd0)), done_sticky, active_request};

    assign S_AXI_AWREADY = S_AXI_ARESETN && !aw_pending && !bvalid_r;
    assign S_AXI_WREADY = S_AXI_ARESETN && !w_pending && !bvalid_r;
    assign S_AXI_BRESP = bresp_r;
    assign S_AXI_BVALID = bvalid_r;
    assign S_AXI_ARREADY = S_AXI_ARESETN && !rvalid_r;
    assign S_AXI_RDATA = rdata_r;
    assign S_AXI_RRESP = rresp_r;
    assign S_AXI_RVALID = rvalid_r;

    gaze_map u_gaze_map (
        .clk(S_AXI_ACLK), .rst_n(core_reset_n), .start(start_pulse),
        .gx_q16(cfg[0]), .gy_q16(cfg[1]),
        .a0(cfg[2]), .a1(cfg[3]), .a2(cfg[4]), .a3(cfg[5]),
        .b0(cfg[6]), .b1(cfg[7]), .b2(cfg[8]), .b3(cfg[9]),
        .width(cfg[10]), .height(cfg[11]), .valid(cfg[12]),
        .busy(core_busy), .done(core_done),
        .screen_x(core_x), .screen_y(core_y), .status(core_status)
    );

    integer i, byte_lane;
    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            aw_pending <= 0; w_pending <= 0;
            aw_saved <= 0; w_saved <= 0; wstrb_saved <= 0;
            bvalid_r <= 0; bresp_r <= 0;
            for (i=0; i<14; i=i+1) cfg[i] <= 0;
            scratch <= 0;
            start_pulse <= 0; soft_reset_pulse <= 0;
            active_request <= 0; done_sticky <= 0; reject_sticky <= 0;
            active_tag <= 0;
            result_x <= 0; result_y <= 0; result_status <= 0; result_tag <= 0;
            complete_count <= 0;
        end else begin
            start_pulse <= 0;
            soft_reset_pulse <= 0;

            if (S_AXI_AWVALID && S_AXI_AWREADY) begin
                aw_saved <= S_AXI_AWADDR;
                aw_pending <= 1;
            end
            if (S_AXI_WVALID && S_AXI_WREADY) begin
                w_saved <= S_AXI_WDATA;
                wstrb_saved <= S_AXI_WSTRB;
                w_pending <= 1;
            end
            if (bvalid_r && S_AXI_BREADY) bvalid_r <= 0;

            // Latch the one-cycle completion. Software can poll DONE later.
            if (core_done && active_request) begin
                active_request <= 0;
                done_sticky <= 1;
                result_x <= core_x;
                result_y <= core_y;
                result_status <= core_status;
                result_tag <= active_tag;
                complete_count <= complete_count + 32'd1;
            end

            if (write_commit) begin
                aw_pending <= 0;
                w_pending <= 0;
                bvalid_r <= 1;
                bresp_r <= 2'b00;
                if (aw_saved[1:0] != 2'b00) begin
                    bresp_r <= 2'b10; // SLVERR: only word-aligned access
                end else if (wstrb_saved == 4'b0000) begin
                    // A fully masked write has no side effect, even on CTRL.
                end else if (write_word == 5'd0) begin
                    // CTRL: bit0 START, bit1 ACK_DONE, bit2 ABORT,
                    //       bit3 CLEAR_REJECT. Higher enabled bits invalid.
                    if (command_word[31:4] != 0) begin
                        bresp_r <= 2'b10;
                    end else if (command_word[2]) begin
                        // Abort has priority, clears calculator/result state,
                        // but preserves staging registers and AXI transactions.
                        soft_reset_pulse <= 1;
                        active_request <= 0; done_sticky <= 0; reject_sticky <= 0;
                        active_tag <= 0;
                        result_x <= 0; result_y <= 0;
                        result_status <= 0; result_tag <= 0; complete_count <= 0;
                    end else begin
                        if (command_word[3]) reject_sticky <= 0;
                        // Ignore ACK while busy, so ACK on a completion edge
                        // cannot erase the newly arriving result.
                        if (command_word[1] && !active_request)
                            done_sticky <= 0;
                        if (command_word[0]) begin
                            if (!active_request && !core_busy && !start_pulse &&
                                !soft_reset_pulse &&
                                (!done_sticky || command_word[1])) begin
                                active_request <= 1;
                                start_pulse <= 1;
                                done_sticky <= 0;
                                active_tag <= cfg[13];
                            end else begin
                                // Do not overwrite a running or unread result.
                                reject_sticky <= 1;
                                bresp_r <= 2'b10;
                            end
                        end
                    end
                end else if (write_word >= 5'd4 && write_word <= 5'd17) begin
                    for (byte_lane=0; byte_lane<4; byte_lane=byte_lane+1)
                        if (wstrb_saved[byte_lane])
                            cfg[write_word-5'd4][byte_lane*8 +: 8] <=
                                w_saved[byte_lane*8 +: 8];
                end else if (write_word == 5'd23) begin
                    for (byte_lane=0; byte_lane<4; byte_lane=byte_lane+1)
                        if (wstrb_saved[byte_lane])
                            scratch[byte_lane*8 +: 8] <= w_saved[byte_lane*8 +: 8];
                end else begin
                    bresp_r <= 2'b10; // read-only or reserved register
                end
            end
        end
    end

    reg [31:0] read_mux;
    always @* begin
        read_mux = 32'd0;
        case (read_word)
            5'd0: read_mux = 32'd0; // write-one command, not a stored bit
            5'd1: read_mux = state_word;
            5'd2: read_mux = 32'h475A4D31; // "GZM1"
            5'd3: read_mux = 32'h00010000; // ABI version 1.0
            5'd18: read_mux = result_x;
            5'd19: read_mux = result_y;
            5'd20: read_mux = result_status;
            5'd21: read_mux = result_tag;
            5'd22: read_mux = complete_count;
            5'd23: read_mux = scratch;
            default: begin
                if (read_word >= 5'd4 && read_word <= 5'd17)
                    read_mux = cfg[read_word-5'd4];
            end
        endcase
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            rvalid_r <= 0; rdata_r <= 0; rresp_r <= 0;
        end else begin
            if (S_AXI_ARVALID && S_AXI_ARREADY) begin
                rvalid_r <= 1;
                if (S_AXI_ARADDR[1:0] != 2'b00) begin
                    rdata_r <= 0;
                    rresp_r <= 2'b10;
                end else begin
                    // Snapshot BEFORE any same-edge register write/completion.
                    rdata_r <= read_mux;
                    rresp_r <= 2'b00;
                end
            end else if (rvalid_r && S_AXI_RREADY) begin
                rvalid_r <= 0;
            end
        end
    end
    // ILA_DEBUG_STAGE5_BEGIN: read-only combinational taps
    (* mark_debug = "true", keep = "true" *) wire ila_gaze_start;
    assign ila_gaze_start = start_pulse;
    (* mark_debug = "true", keep = "true" *) wire ila_gaze_busy;
    assign ila_gaze_busy = core_busy;
    (* mark_debug = "true", keep = "true" *) wire ila_gaze_done;
    assign ila_gaze_done = core_done;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_gaze_gx;
    assign ila_gaze_gx = cfg[0];
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_gaze_gy;
    assign ila_gaze_gy = cfg[1];
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_gaze_result_x;
    assign ila_gaze_result_x = result_x;
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_gaze_result_y;
    assign ila_gaze_result_y = result_y;
    (* mark_debug = "true", keep = "true" *) wire [1:0] ila_gaze_status;
    assign ila_gaze_status = result_status[1:0];
    (* mark_debug = "true", keep = "true" *) wire [31:0] ila_gaze_count;
    assign ila_gaze_count = complete_count;
    // ILA_DEBUG_STAGE5_END
endmodule
