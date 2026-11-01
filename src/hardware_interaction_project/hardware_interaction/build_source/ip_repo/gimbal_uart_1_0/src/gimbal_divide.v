`timescale 1ns / 1ps
// Shared exact unsigned 64/64 divider, separating compare and subtract paths.
module gimbal_divide(input wire clk,rst_n,start,
    input wire [63:0] numerator,denominator,
    output reg busy,done,output reg [63:0] quotient,remainder);
    reg [63:0] n,d,q;
    reg [64:0] rem,shifted;
    reg [6:0] count;
    reg [1:0] phase;
    reg take;
    always @(posedge clk) begin
        if(!rst_n) begin
            busy<=0;done<=0;quotient<=0;remainder<=0;n<=0;d<=0;q<=0;
            rem<=0;shifted<=0;count<=0;phase<=0;take<=0;
        end else begin
            done<=0;
            if(start && !busy) begin
                busy<=1;n<=numerator;d<=denominator;q<=0;rem<=0;count<=0;phase<=0;
            end else if(busy) case(phase)
                0: begin shifted<={rem[63:0],n[63]};n<=n<<1;phase<=1;end
                1: begin take<=shifted>={1'b0,d};phase<=2;end
                2: begin
                    rem<=take ? shifted-{1'b0,d} : shifted;
                    q<={q[62:0],take};phase<=0;
                    if(count==63) begin
                        quotient<={q[62:0],take};
                        remainder<=take ? shifted[63:0]-d : shifted[63:0];
                        busy<=0;done<=1;
                    end else count<=count+1'b1;
                end
            endcase
        end
    end
endmodule
