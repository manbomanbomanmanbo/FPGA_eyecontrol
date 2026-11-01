`timescale 1ns / 1ps
// Rectified disparity to camera-space XYZ. All signed inputs/results are Q16.16.
// This block does not capture cameras, match images, or command any actuator.
module stereo_xyz_core (
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire ack,
    input wire abort,
    input wire signed [31:0] u, v, d, fx, fy, cx, cy, baseline_m,
    input wire signed [31:0] z_min, z_max,
    input wire [31:0] width, height,
    input wire input_valid,
    input wire [31:0] request_tag,
    output reg busy,
    output reg done,
    output reg error,
    output reg signed [31:0] x, y, z,
    output reg [31:0] status,
    output reg [31:0] result_tag,
    output reg [31:0] completion_count
);
    localparam IDLE = 4'd0, VALIDATE = 4'd1, PREPARE_A = 4'd2,
               PREPARE_B = 4'd3, SET_Z = 4'd4, SET_X = 4'd5,
               SET_Y = 4'd6, DIVIDE = 4'd7, CHECK_RESULT = 4'd8;
    localparam ERR_INPUT = 32'd1, ERR_PARAMETER = 32'd2,
               ERR_IMAGE = 32'd3, ERR_DEPTH_CONFIG = 32'd4,
               ERR_DEPTH = 32'd5, ERR_OVERFLOW = 32'd6;

    reg [3:0] phase;
    reg signed [31:0] u_l, v_l, d_l, fx_l, fy_l, cx_l, cy_l, b_l;
    reg signed [31:0] z_min_l, z_max_l;
    reg [31:0] width_l, height_l, tag_l;
    reg valid_l;
    reg signed [31:0] x_l, z_l;
    reg [1:0] div_target;
    reg [6:0] div_iteration;
    reg div_negative;
    reg [95:0] div_work;
    reg [64:0] div_remainder;
    reg [63:0] div_denominator;
    reg signed [95:0] quotient;
    reg signed [32:0] delta_u, delta_v;
    reg signed [63:0] focal_baseline;
    reg [63:0] focal_disparity;
    reg signed [64:0] x_product;
    reg signed [95:0] y_product;

    // Separate difference/first-product and second-product clock stages.
    // The full 97-bit y product is narrowed only after valid positive inputs
    // and in-image coordinates bound its magnitude below 2^94.
    wire signed [32:0] u_ext = {u_l[31], u_l};
    wire signed [32:0] v_ext = {v_l[31], v_l};
    wire signed [32:0] cx_ext = {cx_l[31], cx_l};
    wire signed [32:0] cy_ext = {cy_l[31], cy_l};
    wire signed [96:0] y_product_full = delta_v * focal_baseline;
    wire signed [95:0] z_numerator = {{32{focal_baseline[63]}}, focal_baseline};
    wire signed [95:0] x_numerator = {{31{x_product[64]}}, x_product};
    wire signed [95:0] y_numerator = y_product;
    wire [64:0] remainder_trial = {div_remainder[63:0], div_work[95]};
    wire division_bit = remainder_trial >= {1'b0, div_denominator};
    wire [64:0] remainder_next = division_bit ?
                  remainder_trial - {1'b0, div_denominator} : remainder_trial;
    wire [95:0] work_next = {div_work[94:0], division_bit};
    wire signed [95:0] quotient_next = div_negative ?
                  -$signed(work_next) : $signed(work_next);
    wire quotient_overflow = quotient > 96'sd2147483647 ||
                             quotient < -96'sd2147483648;

    task complete_error;
        input [31:0] code;
        begin
            busy <= 1'b0;
            done <= 1'b1;
            error <= 1'b1;
            x <= 32'sd0;
            y <= 32'sd0;
            z <= 32'sd0;
            status <= code;
            result_tag <= tag_l;
            completion_count <= completion_count + 32'd1;
            phase <= IDLE;
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            phase <= IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
            x <= 32'sd0; y <= 32'sd0; z <= 32'sd0;
            status <= 32'd0;
            result_tag <= 32'd0;
            completion_count <= 32'd0;
            u_l <= 0; v_l <= 0; d_l <= 0; fx_l <= 0; fy_l <= 0;
            cx_l <= 0; cy_l <= 0; b_l <= 0;
            z_min_l <= 0; z_max_l <= 0;
            width_l <= 0; height_l <= 0; tag_l <= 0; valid_l <= 0;
            x_l <= 0; z_l <= 0;
            div_target <= 0; div_iteration <= 0; div_negative <= 0;
            div_work <= 0; div_remainder <= 0; div_denominator <= 0;
            quotient <= 0;
            delta_u <= 0; delta_v <= 0;
            focal_baseline <= 0; focal_disparity <= 0;
            x_product <= 0; y_product <= 0;
        end else if (abort) begin
            phase <= IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
            x <= 0; y <= 0; z <= 0;
            status <= 0;
            result_tag <= 0;
            div_work <= 0; div_remainder <= 0; div_denominator <= 0;
            quotient <= 0; x_l <= 0; z_l <= 0;
            delta_u <= 0; delta_v <= 0;
            focal_baseline <= 0; focal_disparity <= 0;
            x_product <= 0; y_product <= 0;
        end else if (ack && done && !busy) begin
            done <= 1'b0;
            error <= 1'b0;
            x <= 0; y <= 0; z <= 0;
            status <= 0;
            result_tag <= 0;
        end else begin
            case (phase)
                IDLE: if (start && !busy && !done) begin
                    u_l <= u; v_l <= v; d_l <= d; fx_l <= fx; fy_l <= fy;
                    cx_l <= cx; cy_l <= cy; b_l <= baseline_m;
                    z_min_l <= z_min; z_max_l <= z_max;
                    width_l <= width; height_l <= height;
                    tag_l <= request_tag; valid_l <= input_valid;
                    busy <= 1'b1;
                    done <= 1'b0;
                    error <= 1'b0;
                    x <= 0; y <= 0; z <= 0;
                    status <= 0; result_tag <= 0;
                    phase <= VALIDATE;
                end
                VALIDATE: begin
                    if (!valid_l)
                        complete_error(ERR_INPUT);
                    else if (fx_l <= 0 || fy_l <= 0 || b_l <= 0 || d_l <= 0)
                        complete_error(ERR_PARAMETER);
                    else if (width_l == 0 || height_l == 0 ||
                             width_l > 32767 || height_l > 32767 ||
                             u_l < 0 || v_l < 0 || cx_l < 0 || cy_l < 0 ||
                             $unsigned(u_l) >= (width_l << 16) ||
                             $unsigned(v_l) >= (height_l << 16) ||
                             $unsigned(cx_l) >= (width_l << 16) ||
                             $unsigned(cy_l) >= (height_l << 16) ||
                             u_ext < {d_l[31], d_l})
                        complete_error(ERR_IMAGE);
                    else if (z_min_l <= 0 || z_max_l <= z_min_l)
                        complete_error(ERR_DEPTH_CONFIG);
                    else
                        phase <= PREPARE_A;
                end
                PREPARE_A: begin
                    delta_u <= u_ext - cx_ext;
                    delta_v <= v_ext - cy_ext;
                    focal_baseline <= fx_l * b_l;
                    focal_disparity <= {32'd0, fy_l} * {32'd0, d_l};
                    phase <= PREPARE_B;
                end
                PREPARE_B: begin
                    x_product <= delta_u * b_l;
                    y_product <= y_product_full[95:0];
                    phase <= SET_Z;
                end
                SET_Z: begin
                    div_work <= z_numerator;
                    div_denominator <= {32'd0, d_l};
                    div_remainder <= 0;
                    div_negative <= 0;
                    div_iteration <= 0;
                    div_target <= 0;
                    phase <= DIVIDE;
                end
                SET_X: begin
                    div_work <= x_numerator[95] ? -x_numerator : x_numerator;
                    div_denominator <= {32'd0, d_l};
                    div_remainder <= 0;
                    div_negative <= x_numerator[95];
                    div_iteration <= 0;
                    div_target <= 1;
                    phase <= DIVIDE;
                end
                SET_Y: begin
                    div_work <= y_numerator[95] ? -y_numerator : y_numerator;
                    div_denominator <= focal_disparity;
                    div_remainder <= 0;
                    div_negative <= y_numerator[95];
                    div_iteration <= 0;
                    div_target <= 2;
                    phase <= DIVIDE;
                end
                DIVIDE: begin
                    div_work <= work_next;
                    div_remainder <= remainder_next;
                    if (div_iteration == 7'd95) begin
                        quotient <= quotient_next;
                        phase <= CHECK_RESULT;
                    end else
                        div_iteration <= div_iteration + 7'd1;
                end
                CHECK_RESULT: begin
                    if (quotient_overflow)
                        complete_error(ERR_OVERFLOW);
                    else if (div_target == 0) begin
                        if (quotient < {{64{z_min_l[31]}}, z_min_l} ||
                            quotient > {{64{z_max_l[31]}}, z_max_l})
                            complete_error(ERR_DEPTH);
                        else begin
                            z_l <= quotient[31:0];
                            phase <= SET_X;
                        end
                    end else if (div_target == 1) begin
                        x_l <= quotient[31:0];
                        phase <= SET_Y;
                    end else begin
                        x <= x_l;
                        y <= quotient[31:0];
                        z <= z_l;
                        status <= 0;
                        result_tag <= tag_l;
                        completion_count <= completion_count + 32'd1;
                        busy <= 1'b0;
                        done <= 1'b1;
                        error <= 1'b0;
                        phase <= IDLE;
                    end
                end
                default: complete_error(ERR_INPUT);
            endcase
        end
    end
endmodule
