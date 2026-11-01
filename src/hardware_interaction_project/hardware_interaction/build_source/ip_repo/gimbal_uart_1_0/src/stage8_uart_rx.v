`timescale 1ns / 1ps
// Independent centre-sampling 8N1 decoder. Input must already be synchronised.
module stage8_uart_rx #(parameter integer DIVISOR=868)(
    input wire clk,rst_n,rx,
    output reg [7:0] data,output reg valid,framing_error
);
    localparam [1:0] IDLE=0,START=1,DATA=2,STOP=3;
    reg [1:0] state;
    reg [9:0] ticks;
    reg [2:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk) begin
        if(!rst_n) begin state<=IDLE;ticks<=0;bit_index<=0;shift<=0;data<=0;valid<=0;framing_error<=0;end
        else begin
            valid<=0;framing_error<=0;
            case(state)
                IDLE:if(!rx) begin state<=START;ticks<=DIVISOR/2-1;end
                START:if(ticks!=0) ticks<=ticks-1'b1;
                    else if(rx) state<=IDLE;
                    else begin state<=DATA;ticks<=DIVISOR-1;bit_index<=0;end
                DATA:if(ticks!=0) ticks<=ticks-1'b1;
                    else begin
                        shift[bit_index]<=rx;ticks<=DIVISOR-1;
                        if(bit_index==7) state<=STOP;else bit_index<=bit_index+1'b1;
                    end
                STOP:if(ticks!=0) ticks<=ticks-1'b1;
                    else begin
                        state<=IDLE;
                        if(rx) begin data<=shift;valid<=1;end else framing_error<=1;
                    end
            endcase
        end
    end
endmodule
