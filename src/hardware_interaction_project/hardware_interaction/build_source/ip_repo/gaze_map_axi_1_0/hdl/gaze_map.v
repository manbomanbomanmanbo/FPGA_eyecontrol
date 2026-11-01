`timescale 1ns / 1ps
// RTL_STEP1_V1 -- standalone coordinate-mapping kernel (Verilog-2001).
// No camera, networking, mouse, actuator, AXI interface, or blink logic.
//
// gx_q16, gy_q16 and a0..b3 are signed integers / 65536.
// Coefficients are PIXEL-valued, in feature order [gx, gy, gx*gy, 1].
// gx and gy must already be normalized using the PC calibration parameters.
// Rounding: nearest, exact half away from zero, matching reference_map.py.
//
// Protocol (one clock domain):
// - rst_n: synchronous, active low; hold low across >= 2 rising clock edges.
// - Pulse start for one clock only while busy==0. Inputs are snapshotted.
// - Requests while busy==1 are ignored, including the completion edge.
// - done is a ONE-CLOCK completion pulse. Read outputs only with done==1.
// - status==0: valid pixel result. Nonzero: invalid result, outputs are zero.
// - A future AXI wrapper MUST latch completion for software polling.
// - Reset cancels the in-flight request without issuing done.
//
// status: 0=OK, 1=valid/dimension error, 2=coordinate error,
//         3=coefficient error, 4=internal FSM error.
// Valid sizes: 2..8192; valid coordinates: -0.25..1.25;
// coefficient magnitude <= 30000 pixels. These are interface bounds,
// not a statement about a particular user's calibration or gaze range.
//
// Functional RTL only: Vivado synthesis, implementation, timing and board
// validation are still required. A testbench clock is NOT a timing result.

module gaze_map (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               start,
    input  wire signed [31:0] gx_q16,
    input  wire signed [31:0] gy_q16,
    input  wire signed [31:0] a0,
    input  wire signed [31:0] a1,
    input  wire signed [31:0] a2,
    input  wire signed [31:0] a3,
    input  wire signed [31:0] b0,
    input  wire signed [31:0] b1,
    input  wire signed [31:0] b2,
    input  wire signed [31:0] b3,
    input  wire        [31:0] width,
    input  wire        [31:0] height,
    input  wire        [31:0] valid,
    output reg                busy,
    output reg                done,
    output reg         [31:0] screen_x,
    output reg         [31:0] screen_y,
    output reg         [31:0] status
);

    localparam [2:0] S_IDLE  = 3'd0,
                     S_CHECK = 3'd1,
                     S_CROSS = 3'd2,
                     S_MUL   = 3'd3,
                     S_SUM   = 3'd4,
                     S_OUT   = 3'd5;
    reg [2:0] state;
    reg signed [31:0] gx_r, gy_r;
    reg signed [31:0] a0_r, a1_r, a2_r, a3_r;
    reg signed [31:0] b0_r, b1_r, b2_r, b3_r;
    reg [31:0] width_r, height_r, valid_r;
    reg signed [63:0] xy_product;
    reg signed [31:0] xy_q16;
    reg signed [63:0] ax0, ax1, ax2, ay0, ay1, ay2;
    reg signed [63:0] sx_sum, sy_sum;

    // Explicitly signed 64-bit constants prevent 32-bit shift truncation.
    wire signed [63:0] a3_term = $signed({{32{a3_r[31]}}, a3_r}) <<< 16;
    wire signed [63:0] b3_term = $signed({{32{b3_r[31]}}, b3_r}) <<< 16;

    function coeff_bad;
        input signed [31:0] c;
        begin
            coeff_bad = (c > 32'sd1966080000) ||
                        (c < -32'sd1966080000);
        end
    endfunction

    // Convert gx*gy from scale 2^32 to scale 2^16.
    // The input validation guarantees the rounded result fits signed 32 bits.
    function signed [31:0] round_cross;
        input signed [63:0] n;
        reg signed [63:0] rounded;
        begin
            if (n >= 64'sd0)
                rounded = (n + 64'sd32768) >>> 16;
            else
                rounded = -(((-n) + 64'sd32768) >>> 16);
            round_cross = rounded[31:0];
        end
    endfunction

    // Convert pixel sum (scale 2^32) to a rounded and clamped pixel index.
    // Called only after validating size; size-1 cannot underflow here.
    function [31:0] to_pixel;
        input signed [63:0] n;
        input        [31:0] size;
        reg signed [63:0] rounded;
        reg signed [63:0] size_signed;
        begin
            size_signed = $signed({32'd0, size});
            if (n >= 64'sd0)
                rounded = (n + 64'sd2147483648) >>> 32;
            else
                rounded = -(((-n) + 64'sd2147483648) >>> 32);
            if (rounded < 64'sd0)
                to_pixel = 32'd0;
            else if (rounded >= size_signed)
                to_pixel = size - 32'd1;
            else
                to_pixel = rounded[31:0];
        end
    endfunction

    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            screen_x <= 32'd0;
            screen_y <= 32'd0;
            status <= 32'd0;
            gx_r <= 0; gy_r <= 0;
            a0_r <= 0; a1_r <= 0; a2_r <= 0; a3_r <= 0;
            b0_r <= 0; b1_r <= 0; b2_r <= 0; b3_r <= 0;
            width_r <= 0; height_r <= 0; valid_r <= 0;
            xy_product <= 0; xy_q16 <= 0;
            ax0 <= 0; ax1 <= 0; ax2 <= 0;
            ay0 <= 0; ay1 <= 0; ay2 <= 0;
            sx_sum <= 0; sy_sum <= 0;
        end else begin
            // A consumer in this clock domain must sample the done pulse.
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        gx_r <= gx_q16; gy_r <= gy_q16;
                        a0_r <= a0; a1_r <= a1;
                        a2_r <= a2; a3_r <= a3;
                        b0_r <= b0; b1_r <= b1;
                        b2_r <= b2; b3_r <= b3;
                        width_r <= width; height_r <= height;
                        valid_r <= valid;
                        busy <= 1'b1;
                        state <= S_CHECK;
                    end
                end
                S_CHECK: begin
                    if ((valid_r != 32'd1) ||
                        (width_r < 32'd2) || (width_r > 32'd8192) ||
                        (height_r < 32'd2) || (height_r > 32'd8192)) begin
                        screen_x <= 0; screen_y <= 0; status <= 32'd1;
                        busy <= 0; done <= 1; state <= S_IDLE;
                    end else if ((gx_r < -32'sd16384) || (gx_r > 32'sd81920) ||
                                 (gy_r < -32'sd16384) || (gy_r > 32'sd81920)) begin
                        screen_x <= 0; screen_y <= 0; status <= 32'd2;
                        busy <= 0; done <= 1; state <= S_IDLE;
                    end else if (coeff_bad(a0_r) || coeff_bad(a1_r) ||
                                 coeff_bad(a2_r) || coeff_bad(a3_r) ||
                                 coeff_bad(b0_r) || coeff_bad(b1_r) ||
                                 coeff_bad(b2_r) || coeff_bad(b3_r)) begin
                        screen_x <= 0; screen_y <= 0; status <= 32'd3;
                        busy <= 0; done <= 1; state <= S_IDLE;
                    end else begin
                        xy_product <= gx_r * gy_r;
                        state <= S_CROSS;
                    end
                end
                S_CROSS: begin
                    xy_q16 <= round_cross(xy_product);
                    state <= S_MUL;
                end
                S_MUL: begin
                    ax0 <= a0_r * gx_r;
                    ax1 <= a1_r * gy_r;
                    ax2 <= a2_r * xy_q16;
                    ay0 <= b0_r * gx_r;
                    ay1 <= b1_r * gy_r;
                    ay2 <= b2_r * xy_q16;
                    state <= S_SUM;
                end
                S_SUM: begin
                    sx_sum <= (ax0 + ax1) + (ax2 + a3_term);
                    sy_sum <= (ay0 + ay1) + (ay2 + b3_term);
                    state <= S_OUT;
                end
                S_OUT: begin
                    screen_x <= to_pixel(sx_sum, width_r);
                    screen_y <= to_pixel(sy_sum, height_r);
                    status <= 32'd0;
                    busy <= 1'b0;
                    done <= 1'b1;
                    state <= S_IDLE;
                end
                default: begin
                    screen_x <= 0; screen_y <= 0; status <= 32'd4;
                    busy <= 0; done <= 1; state <= S_IDLE;
                end
            endcase
        end
    end
endmodule
