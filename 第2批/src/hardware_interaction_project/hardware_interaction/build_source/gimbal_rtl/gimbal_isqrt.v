`timescale 1ns / 1ps
// Exact floor(sqrt(unsigned 80-bit radicand)); three short stages per bit pair.
module gimbal_isqrt(input wire clk,rst_n,start,input wire [79:0] radicand,
                    output reg busy,done,output reg [39:0] result);
    reg [79:0] n;
    reg [43:0] rem,shifted,trial;
    reg [39:0] root;
    reg [5:0] count;
    reg [1:0] phase;
    reg take;
    always @(posedge clk) begin
        if(!rst_n) begin
            busy<=0;done<=0;result<=0;n<=0;rem<=0;shifted<=0;trial<=0;
            root<=0;count<=0;phase<=0;take<=0;
        end else begin
            done<=0;
            if(start && !busy) begin
                busy<=1;n<=radicand;rem<=0;root<=0;count<=0;phase<=0;
            end else if(busy) case(phase)
                0: begin
                    shifted<=(rem<<2)|{42'd0,n[79:78]};
                    trial<={2'd0,root,2'b01};n<=n<<2;phase<=1;
                end
                1: begin take<=shifted>=trial;phase<=2;end
                2: begin
                    rem<=take ? shifted-trial : shifted;
                    root<={root[38:0],take};phase<=0;
                    if(count==39) begin
                        result<={root[38:0],take};busy<=0;done<=1;
                    end else count<=count+1'b1;
                end
            endcase
        end
    end
endmodule
