`timescale 1ns / 1ps
// atan2(y,x), x>0: 32 vectoring iterations. Coordinates are signed Q56,
// angle is signed Q32 degrees. Tables are round-to-nearest/ties-to-even.
module gimbal_cordic(input wire clk,rst_n,start,
    input wire signed [79:0] x_in,y_in,
    output reg busy,done,output reg signed [47:0] angle);
    reg signed [79:0] x,y,negative_x,shift_x,shift_y,next_x,next_y;
    reg signed [47:0] z,table_angle,next_z;
    reg [5:0] index;
    reg [1:0] phase;
    reg positive,initial_zero,initial_positive_diagonal,initial_x_positive;
    function signed [47:0] atan_table;
        input [5:0] i;
        begin case(i)
            0:atan_table=48'sd193273528320; 1:atan_table=48'sd114096026022;
            2:atan_table=48'sd60285206653; 3:atan_table=48'sd30601712202;
            4:atan_table=48'sd15360239180; 5:atan_table=48'sd7687607525;
            6:atan_table=48'sd3844741810; 7:atan_table=48'sd1922488225;
            8:atan_table=48'sd961258780; 9:atan_table=48'sd480631223;
            10:atan_table=48'sd240315841; 11:atan_table=48'sd120157949;
            12:atan_table=48'sd60078978; 13:atan_table=48'sd30039490;
            14:atan_table=48'sd15019745; 15:atan_table=48'sd7509872;
            16:atan_table=48'sd3754936; 17:atan_table=48'sd1877468;
            18:atan_table=48'sd938734; 19:atan_table=48'sd469367;
            20:atan_table=48'sd234684; 21:atan_table=48'sd117342;
            22:atan_table=48'sd58671; 23:atan_table=48'sd29335;
            24:atan_table=48'sd14668; 25:atan_table=48'sd7334;
            26:atan_table=48'sd3667; 27:atan_table=48'sd1833;
            28:atan_table=48'sd917; 29:atan_table=48'sd458;
            30:atan_table=48'sd229; 31:atan_table=48'sd115;
            default:atan_table=0;
        endcase end
    endfunction
    always @(posedge clk) begin
        if(!rst_n) begin
            busy<=0;done<=0;angle<=0;x<=0;y<=0;negative_x<=0;z<=0;index<=0;phase<=0;
            shift_x<=0;shift_y<=0;next_x<=0;next_y<=0;table_angle<=0;next_z<=0;positive<=0;
            initial_zero<=0;initial_positive_diagonal<=0;initial_x_positive<=0;
        end else begin
            done<=0;
            if(start && !busy) begin
                // Register -x separately from the equality comparison, keeping
                // an 80-bit negation and wide compare out of one clock path.
                x<=x_in;y<=y_in;negative_x<=-x_in;z<=0;index<=0;phase<=3;busy<=1;
                initial_zero<=y_in==0;initial_positive_diagonal<=x_in>0 && y_in==x_in;
                initial_x_positive<=x_in>0;
            end else if(busy) case(phase)
                3: begin
                    if(initial_zero) begin angle<=0;done<=1;busy<=0;end
                    else if(initial_positive_diagonal) begin angle<=48'sd193273528320;done<=1;busy<=0;end
                    else if(initial_x_positive && y==negative_x) begin angle<=-48'sd193273528320;done<=1;busy<=0;end
                    else phase<=0;
                end
                0: begin
                    shift_x<=x>>>index;shift_y<=y>>>index;
                    table_angle<=atan_table(index);positive<=y>=0;phase<=1;
                end
                1: begin
                    next_x<=positive ? x+shift_y : x-shift_y;
                    next_y<=positive ? y-shift_x : y+shift_x;
                    next_z<=positive ? z+table_angle : z-table_angle;phase<=2;
                end
                2: begin
                    x<=next_x;y<=next_y;z<=next_z;phase<=0;
                    if(index==31) begin angle<=next_z;done<=1;busy<=0;end
                    else index<=index+1'b1;
                end
            endcase
        end
    end
endmodule
