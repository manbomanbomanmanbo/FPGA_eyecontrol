`timescale 1ns / 1ps
// 100 MHz / 868, 8N1. done marks the end of the FULL final stop bit.
module stage8_uart_tx #(parameter integer DIVISOR=868)(
    input wire clk,rst_n,start,input wire [7:0] data,
    output reg tx,busy,done
);
    reg [9:0] shift;
    reg [9:0] ticks;
    reg [3:0] bit_index;
    always @(posedge clk) begin
        if(!rst_n) begin tx<=1;busy<=0;done<=0;shift<=10'h3ff;ticks<=0;bit_index<=0;end
        else begin
            done<=0;
            if(!busy) begin
                tx<=1;
                if(start) begin
                    shift<={1'b1,data,1'b0};tx<=0;busy<=1;ticks<=DIVISOR-1;bit_index<=0;
                end
            end else if(ticks!=0) ticks<=ticks-1'b1;
            else if(bit_index==9) begin tx<=1;busy<=0;done<=1;end
            else begin
                bit_index<=bit_index+1'b1;shift<={1'b1,shift[9:1]};tx<=shift[1];ticks<=DIVISOR-1;
            end
        end
    end
endmodule
