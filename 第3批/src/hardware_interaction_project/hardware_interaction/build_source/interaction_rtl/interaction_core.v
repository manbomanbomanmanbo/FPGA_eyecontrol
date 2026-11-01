`timescale 1ns / 1ps
// Interaction decisions and all arithmetic execute in the 100 MHz PL domain.
module interaction_core #(
    parameter integer CLK_HZ = 100000000
) (
    input wire clk, input wire rst_n,
    input wire start, input wire ack_done, input wire reset_state,
    input wire [31:0] op, a, b, c, d, e, f, g, h, request_tag,
    output reg busy, output reg done,
    output wire error,
    output reg [31:0] out0, out1, out2, out3, out4, out5, out6, out7,
    output reg [31:0] status, result_tag, completion_count
);
    localparam integer CLOCKS_PER_MS = CLK_HZ / 1000;
    localparam [3:0] IDLE=0, EXEC=1, DIFF=2, SQUARE=3, SQ_INIT=4,
                     SQRT=5, ALPHA=6, MULTIPLY=7, APPLY=8, ANCHOR=9, DIVIDE=10,
                     MOVE_BLEND=11, MOVE_ABS=12, SCREEN_BLEND=13, SCREEN_DIV=14;
    reg [3:0] state;
    reg [31:0] req_op, ra, rb, rc, rd, re, rf, rg, rh, op_tick;
    reg [31:0] tick_count, uptime_ms;
    wire ms_tick = tick_count == CLOCKS_PER_MS-1;
    wire [31:0] now_ms = uptime_ms + (ms_tick ? 32'd1 : 32'd0);

    reg smooth_seeded;
    reg signed [31:0] smooth_x, smooth_y;
    reg signed [32:0] dx, dy;
    reg [63:0] square_x, square_y, radicand;
    reg [65:0] sqrt_remainder;
    reg [31:0] sqrt_root, distance_q;
    reg [5:0] sqrt_count;
    reg [16:0] alpha_q;
    reg signed [63:0] product_x, product_y;
    wire [65:0] sqrt_shifted = (sqrt_remainder << 2) | {64'd0,radicand[63:62]};
    wire [65:0] sqrt_trial = {32'd0,sqrt_root,2'b01};
    wire sqrt_take = sqrt_shifted >= sqrt_trial;
    wire [31:0] sqrt_next = (sqrt_root << 1) | (sqrt_take ? 32'd1 : 32'd0);
    wire [31:0] capped_distance = distance_q > 32'd1310720 ? 32'd1310720 : distance_q;
    // A shared exact unsigned divider keeps odd denominators out of long
    // combinational paths. Numerators fit forty bits; remainder is <50.
    reg [39:0] div_nx, div_ny, div_qx, div_qy;
    reg [6:0] div_rx, div_ry;
    reg [5:0] div_den, div_count;
    reg [1:0] div_kind;
    reg div_negative_x, div_negative_y;
    wire [6:0] div_shift_x={div_rx[5:0],div_nx[39]};
    wire [6:0] div_shift_y={div_ry[5:0],div_ny[39]};
    wire div_take_x=div_shift_x>={1'b0,div_den};
    wire div_take_y=div_shift_y>={1'b0,div_den};
    wire [39:0] div_next_x=(div_qx<<1)|(div_take_x ? 40'd1 : 40'd0);
    wire [39:0] div_next_y=(div_qy<<1)|(div_take_y ? 40'd1 : 40'd0);
    wire [31:0] divided_alpha=32'd49152-div_next_x[31:0];
    wire [31:0] divided_move_x=div_negative_x ? -div_next_x[31:0] : div_next_x[31:0];
    wire [31:0] divided_move_y=div_negative_y ? -div_next_y[31:0] : div_next_y[31:0];

    reg [31:0] screen_x, screen_y;
    reg [31:0] history_x [0:29];
    reg [31:0] history_y [0:29];
    reg [4:0] history_count, history_next;
    reg [3:0] remaining;
    reg lock_active;
    reg [35:0] history_sum_x, history_sum_y;
    reg [2:0] average_count;
    reg [35:0] sum_x, sum_y;
    integer index;
    reg signed [31:0] mouth_direction;
    reg [2:0] mouth_counter;
    reg scroll_time_valid;
    reg [31:0] last_scroll;
    reg move_seeded, hold_valid;
    reg signed [31:0] move_x, move_y;
    reg [31:0] hold_until;
    reg signed [31:0] temporary_x, temporary_y, move_previous_x, move_previous_y;
    reg signed [34:0] move_weighted_x, move_weighted_y;
    reg [35:0] screen_weighted_x, screen_weighted_y;
    reg pending, last_blink_valid, last_action_valid;
    reg [31:0] last_blink, last_action;
    reg [31:0] offer_id, offer_counter, offer_action, offer_tick;

    wire signed [31:0] action_age = $signed(op_tick-last_action);
    wire signed [31:0] blink_age = $signed(op_tick-last_blink);
    wire pending_expired = pending && last_blink_valid && blink_age >= 32'sd1500;
    wire pending_live = pending && !pending_expired;
    wire double_window = pending_live && last_blink_valid &&
                         blink_age > 32'sd150 && blink_age < 32'sd1500;
    wire action_gate = !last_action_valid || action_age > 32'sd350 ||
                      (ra==1 && rb==0 && double_window);
    wire [31:0] planned_action = !action_gate || ra==0 ? 0 :
        ra==2 ? 3 : rb==1 ? 4 : double_window ? 2 : 1;
    wire mapped_valid = rc[1] && rc[2];
    wire [31:0] clamped_screen_x = clamp_pixel($signed(ra),rd,0);
    wire [31:0] clamped_screen_y = clamp_pixel($signed(rb),re,0);
    wire signed [31:0] target_move_x = clamp_pixel($signed(ra),rc,10);
    wire signed [31:0] target_move_y = clamp_pixel($signed(rb),rd,10);
    wire signed [31:0] previous_move_x = move_seeded ? move_x : $signed((rc>>1)<<16);
    wire signed [31:0] previous_move_y = move_seeded ? move_y : $signed((rd>>1)<<16);
    wire [31:0] new_screen_x = rc[1] ? clamped_screen_x : screen_x;
    wire [31:0] new_screen_y = rc[1] ? clamped_screen_y : screen_y;
    wire [2:0] new_mouth_counter = mouth_counter < 5 ? mouth_counter+1'b1 : 3'd5;
    wire scroll_due = !scroll_time_valid || $signed(op_tick-last_scroll)>32'sd10;
    assign error = status != 0;

    function [31:0] clamp_pixel;
        input signed [31:0] value;
        input [31:0] dimension;
        input integer margin;
        reg signed [31:0] lower, upper;
        begin
            // Every operation validates dimension<=16383 before consuming this.
            // Thus both Q16 bounds are representable in signed 32 bits.
            lower = margin*32'sd65536;
            upper = (dimension-margin)<<16;
            if ($signed(value)<lower) clamp_pixel=lower[31:0];
            else if ($signed(value)>upper) clamp_pixel=upper[31:0];
            else clamp_pixel=value;
        end
    endfunction
    function [4:0] ring_index;
        input [4:0] next_position;
        input integer back;
        integer position;
        begin
            position=next_position-back;
            if(position<0) position=position+30;
            ring_index=position;
        end
    endfunction
    function signed [63:0] round_shift16;
        input signed [63:0] value;
        begin
            if(value<0) round_shift16=-((-value+64'sd32768)>>>16);
            else round_shift16=(value+64'sd32768)>>>16;
        end
    endfunction
    task begin_division;
        input [39:0] numerator_x,numerator_y;
        input [5:0] denominator;
        input [1:0] kind;
        begin
            div_nx<=numerator_x;div_ny<=numerator_y;
            div_qx<=0;div_qy<=0;div_rx<=0;div_ry<=0;div_count<=0;
            div_den<=denominator;div_kind<=kind;state<=DIVIDE;
        end
    endtask
    task finish;
        input [31:0] code;
        begin
            status<=code; busy<=0; done<=1; state<=IDLE;
            completion_count<=completion_count+1'b1;
        end
    endtask
    task screen_result;
        input [31:0] x,y;
        input [3:0] frames;
        input [4:0] count;
        input active;
        begin
            out0<=x; out1<=y; out2<={28'd0,frames};
            out3<={27'd0,count}; out4<={31'd0,active};
        end
    endtask

    // Only up to five samples are ever averaged. The ring retains thirty.
    always @* begin
        sum_x=0; sum_y=0;
        if(history_count>=6) begin
            for(index=3;index<=6;index=index+1) begin
                sum_x=sum_x+history_x[ring_index(history_next,index)];
                sum_y=sum_y+history_y[ring_index(history_next,index)];
            end
        end else begin
            for(index=0;index<5;index=index+1) begin
                if(index<history_count) begin
                    sum_x=sum_x+history_x[index];
                    sum_y=sum_y+history_y[index];
                end
            end
        end
    end

    always @(posedge clk) begin
        if(!rst_n || reset_state) begin
            busy<=0; done<=0; state<=IDLE; status<=0; result_tag<=0; completion_count<=0;
            out0<=0;out1<=0;out2<=0;out3<=0;out4<=0;out5<=0;out6<=0;out7<=0;
            req_op<=0;ra<=0;rb<=0;rc<=0;rd<=0;re<=0;rf<=0;rg<=0;rh<=0;op_tick<=0;
            tick_count<=0;uptime_ms<=0;
            smooth_seeded<=0;smooth_x<=0;smooth_y<=0;
            dx<=0;dy<=0;square_x<=0;square_y<=0;radicand<=0;
            sqrt_remainder<=0;sqrt_root<=0;sqrt_count<=0;distance_q<=0;
            alpha_q<=0;product_x<=0;product_y<=0;
            div_nx<=0;div_ny<=0;div_qx<=0;div_qy<=0;div_rx<=0;div_ry<=0;
            div_den<=0;div_count<=0;div_kind<=0;div_negative_x<=0;div_negative_y<=0;
            screen_x<=0;screen_y<=0;history_count<=0;history_next<=0;
            remaining<=0;lock_active<=0;history_sum_x<=0;history_sum_y<=0;average_count<=0;
            // Entries need no reset: history_count excludes every unwritten slot.
            mouth_direction<=-32'sd1;mouth_counter<=0;scroll_time_valid<=0;last_scroll<=0;
            move_seeded<=0;hold_valid<=0;move_x<=0;move_y<=0;hold_until<=0;
            temporary_x<=0;temporary_y<=0;move_previous_x<=0;move_previous_y<=0;
            move_weighted_x<=0;move_weighted_y<=0;screen_weighted_x<=0;screen_weighted_y<=0;
            pending<=0;last_blink_valid<=0;last_action_valid<=0;
            last_blink<=0;last_action<=0;offer_id<=0;offer_counter<=0;offer_action<=0;offer_tick<=0;
        end else begin
            if(ms_tick) begin tick_count<=0;uptime_ms<=uptime_ms+1'b1;end
            else tick_count<=tick_count+1'b1;
            if(ack_done && done && !busy) begin done<=0;status<=0;end
            if(start && !busy && !done) begin
                busy<=1;done<=0;status<=0;state<=EXEC;
                req_op<=op;ra<=a;rb<=b;rc<=c;rd<=d;re<=e;rf<=f;rg<=g;rh<=h;
                op_tick<=now_ms;result_tag<=request_tag;
                out0<=0;out1<=0;out2<=0;out3<=0;out4<=0;out5<=0;out6<=0;out7<=0;
            end else case(state)
                EXEC: case(req_op)
                    1: begin
                        if($signed(ra)<0 || $signed(rb)<0 || ra>32'd536870912 || rb>32'd536870912) finish(2);
                        else if(!smooth_seeded) begin
                            smooth_seeded<=1;smooth_x<=ra;smooth_y<=rb;out0<=ra;out1<=rb;finish(0);
                        end else state<=DIFF;
                    end
                    2: begin
                        if(rc[31:3]!=0 || rd<2 || rd>16383 || re<2 || re>16383) finish(2);
                        else if(rc[0]) begin
                            remaining<=8;lock_active<=1;
                            if(!lock_active && history_count>=3) begin
                                history_sum_x<=sum_x;history_sum_y<=sum_y;
                                average_count<=history_count>=6 ? 3'd4 : history_count[2:0];
                                state<=ANCHOR;
                            end else begin screen_result(screen_x,screen_y,8,history_count,1);finish(0);end
                        end else if(lock_active && remaining>0) begin
                            remaining<=remaining-1'b1;
                            if(remaining<=3 && history_count!=0 && mapped_valid) begin
                                temporary_x<=clamped_screen_x;temporary_y<=clamped_screen_y;state<=SCREEN_BLEND;
                            end else begin
                                screen_result(screen_x,screen_y,remaining-1'b1,history_count,1);finish(0);
                            end
                        end else begin
                            lock_active<=0;remaining<=0;
                            screen_x<=new_screen_x;screen_y<=new_screen_y;
                            history_x[history_next]<=new_screen_x;history_y[history_next]<=new_screen_y;
                            history_next<=history_next==29 ? 0 : history_next+1'b1;
                            if(history_count<30) history_count<=history_count+1'b1;
                            screen_result(new_screen_x,new_screen_y,0,history_count<30 ? history_count+1'b1 : history_count,0);
                            finish(0);
                        end
                    end
                    3: begin
                        if(ra>2 || rb>1) finish(2);
                        else if(offer_id==0 && planned_action!=0 && offer_counter==32'hffffffff) finish(2);
                        else begin
                            if(pending_expired) pending<=0;
                            out2<={31'd0,pending_live};out3<={31'd0,pending_expired};out4<=op_tick;
                            if(offer_id!=0 || planned_action==0) begin out0<=0;out1<=offer_id;end
                            else begin
                                offer_counter<=offer_counter+1'b1;offer_id<=offer_counter+1'b1;
                                offer_action<=planned_action;offer_tick<=op_tick;
                                out0<=planned_action;out1<=offer_counter+1'b1;
                            end
                            finish(0);
                        end
                    end
                    4: begin
                        if(rb>1) finish(2);
                        else if(offer_id==0) finish(4);
                        else if(ra!=offer_id) finish(3);
                        else begin
                            last_action_valid<=1;
                            case(offer_action)
                                1: begin
                                    pending<=rb[0];out0<=rb;last_blink<=offer_tick;
                                    last_blink_valid<=1;last_action<=offer_tick;
                                end
                                2: begin pending<=0;out0<=0;last_action<=op_tick+300;end
                                4: begin pending<=0;out0<=0;last_action<=offer_tick+300;end
                                default: begin pending<=0;out0<=0;last_action<=offer_tick;end
                            endcase
                            offer_id<=0;offer_action<=0;offer_tick<=0;out1<=op_tick;finish(0);
                        end
                    end
                    5: begin
                        if(rc>1) finish(2);
                        else if(!rc[0]) begin
                            if($signed(ra)>=32'sd6554) begin mouth_direction<=1;out1<=1;end
                            else if($signed(rb)>=32'sd6554) begin mouth_direction<=-32'sd1;out1<=32'hffffffff;end
                            else out1<=mouth_direction;
                            mouth_counter<=0;out0<=0;out2<=0;finish(0);
                        end else begin
                            mouth_counter<=new_mouth_counter;out1<=mouth_direction;out2<={29'd0,new_mouth_counter};
                            if(new_mouth_counter>=3 && scroll_due) begin
                                out0<=mouth_direction;last_scroll<=op_tick;scroll_time_valid<=1;
                            end else out0<=0;
                            finish(0);
                        end
                    end
                    6: begin
                        if(rc<=20 || rd<=20 || rc>16383 || rd>16383) finish(2);
                        else if(hold_valid && $signed(op_tick-hold_until)<0) begin
                            out0<=move_x;out1<=move_y;out2<=0;finish(0);
                        end else begin
                            temporary_x<=target_move_x;temporary_y<=target_move_y;
                            move_previous_x<=previous_move_x;move_previous_y<=previous_move_y;state<=MOVE_BLEND;
                        end
                    end
                    7: begin
                        move_seeded<=1;move_x<=ra;move_y<=rb;hold_valid<=1;hold_until<=op_tick+200;
                        out0<=ra;out1<=rb;out2<=op_tick+200;finish(0);
                    end
                    8: begin
                        screen_result(screen_x,screen_y,remaining,history_count,lock_active);
                        out5<={31'd0,pending};out6<=offer_id;out7<=op_tick;finish(0);
                    end
                    9: begin
                        pending<=0;last_blink_valid<=0;last_action_valid<=0;
                        offer_id<=0;offer_action<=0;offer_tick<=0;scroll_time_valid<=0;finish(0);
                    end
                    default: finish(1);
                endcase
                DIFF: begin
                    dx<=$signed({ra[31],ra})-$signed({smooth_x[31],smooth_x});
                    dy<=$signed({rb[31],rb})-$signed({smooth_y[31],smooth_y});state<=SQUARE;
                end
                SQUARE: begin square_x<=dx*dx;square_y<=dy*dy;state<=SQ_INIT;end
                SQ_INIT: begin
                    radicand<=square_x+square_y;sqrt_remainder<=0;sqrt_root<=0;sqrt_count<=0;state<=SQRT;
                end
                SQRT: begin
                    radicand<=radicand<<2;
                    sqrt_remainder<=sqrt_take ? sqrt_shifted-sqrt_trial : sqrt_shifted;
                    sqrt_root<=sqrt_next;
                    if(sqrt_count==31) begin distance_q<=sqrt_next;state<=ALPHA;end
                    else sqrt_count<=sqrt_count+1'b1;
                end
                ALPHA: begin
                    if(distance_q<65536) begin out0<=smooth_x;out1<=smooth_y;finish(0);end
                    else begin
                        begin_division({8'd0,capped_distance}+40'd25,0,50,0);
                    end
                end
                MULTIPLY: begin
                    product_x<=dx*$signed(18'd65536-{1'b0,alpha_q});
                    product_y<=dy*$signed(18'd65536-{1'b0,alpha_q});state<=APPLY;
                end
                APPLY: begin
                    smooth_x<=$signed(smooth_x)+round_shift16(product_x);
                    smooth_y<=$signed(smooth_y)+round_shift16(product_y);
                    out0<=$signed(smooth_x)+round_shift16(product_x);
                    out1<=$signed(smooth_y)+round_shift16(product_y);finish(0);
                end
                ANCHOR: begin
                    case(average_count)
                        3:begin_division({4'd0,history_sum_x}+40'd1,{4'd0,history_sum_y}+40'd1,3,1);
                        4:begin_division({4'd0,history_sum_x}+40'd2,{4'd0,history_sum_y}+40'd2,4,1);
                        default:begin_division({4'd0,history_sum_x}+40'd2,{4'd0,history_sum_y}+40'd2,5,1);
                    endcase
                end
                DIVIDE: begin
                    div_nx<=div_nx<<1;div_ny<=div_ny<<1;
                    div_rx<=div_take_x ? div_shift_x-{1'b0,div_den} : div_shift_x;
                    div_ry<=div_take_y ? div_shift_y-{1'b0,div_den} : div_shift_y;
                    div_qx<=div_next_x;div_qy<=div_next_y;
                    if(div_count==39) begin
                        case(div_kind)
                            0:begin
                                alpha_q<=divided_alpha<22938 ? 17'd22938 : divided_alpha>49152 ? 17'd49152 : divided_alpha[16:0];
                                state<=MULTIPLY;
                            end
                            1:begin
                                screen_x<=div_next_x[31:0];screen_y<=div_next_y[31:0];
                                screen_result(div_next_x[31:0],div_next_y[31:0],8,history_count,1);finish(0);
                            end
                            2:begin
                                screen_x<=div_next_x[31:0];screen_y<=div_next_y[31:0];
                                screen_result(div_next_x[31:0],div_next_y[31:0],remaining,history_count,1);finish(0);
                            end
                            default:begin
                                move_x<=divided_move_x;move_y<=divided_move_y;move_seeded<=1;hold_valid<=0;
                                out0<=divided_move_x;out1<=divided_move_y;out2<=1;finish(0);
                            end
                        endcase
                    end else div_count<=div_count+1'b1;
                end
                MOVE_BLEND:begin
                    move_weighted_x<=$signed({move_previous_x[31],move_previous_x,2'b00})+
                                     $signed({{3{temporary_x[31]}},temporary_x});
                    move_weighted_y<=$signed({move_previous_y[31],move_previous_y,2'b00})+
                                     $signed({{3{temporary_y[31]}},temporary_y});
                    state<=MOVE_ABS;
                end
                MOVE_ABS:begin
                    div_negative_x<=move_weighted_x[34];div_negative_y<=move_weighted_y[34];
                    begin_division(move_weighted_x<0 ? -move_weighted_x+35'sd2 : move_weighted_x+35'sd2,
                                   move_weighted_y<0 ? -move_weighted_y+35'sd2 : move_weighted_y+35'sd2,5,3);
                end
                SCREEN_BLEND:begin
                    screen_weighted_x<=(screen_x*36'd7)+(temporary_x*36'd3);
                    screen_weighted_y<=(screen_y*36'd7)+(temporary_y*36'd3);state<=SCREEN_DIV;
                end
                SCREEN_DIV:begin
                    begin_division({4'd0,screen_weighted_x}+40'd5,{4'd0,screen_weighted_y}+40'd5,10,2);
                end
                default: begin end
            endcase
        end
    end
endmodule
