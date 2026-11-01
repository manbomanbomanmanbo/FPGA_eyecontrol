`timescale 1ns / 1ps
// GAZE_AXI_STEP2_TB_V1 -- self-checking integration test, no board needed.
// Drive at falling edges, sample at rising edges to avoid DUT/testbench races.
module tb_gaze_map_axi;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0;
    reg [6:0] awaddr = 0, araddr = 0;
    reg [31:0] wdata = 0;
    reg [3:0] wstrb = 0;
    reg awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    wire awready,wready,bvalid,arready,rvalid;
    wire [1:0] bresp,rresp;
    wire [31:0] rdata;
    integer checks=0;
    reg [31:0] tmp, held, old_count;

    gaze_map_axi dut (
        .s00_axi_aclk(clk), .s00_axi_aresetn(rst_n),
        .s00_axi_awaddr(awaddr), .s00_axi_awprot(3'd0),
        .s00_axi_awvalid(awvalid), .s00_axi_awready(awready),
        .s00_axi_wdata(wdata), .s00_axi_wstrb(wstrb),
        .s00_axi_wvalid(wvalid), .s00_axi_wready(wready),
        .s00_axi_bresp(bresp), .s00_axi_bvalid(bvalid), .s00_axi_bready(bready),
        .s00_axi_araddr(araddr), .s00_axi_arprot(3'd0),
        .s00_axi_arvalid(arvalid), .s00_axi_arready(arready),
        .s00_axi_rdata(rdata), .s00_axi_rresp(rresp),
        .s00_axi_rvalid(rvalid), .s00_axi_rready(rready)
    );

    task automatic require(input bit condition, input string label);
        begin
            checks = checks + 1;
            if (!condition) begin
                $display("AXI_TEST_FAILED: %s at time=%0t", label, $time);
                $fatal(1, "AXI integration test failed");
            end
        end
    endtask

    // Fails rather than printing success if any bus transaction never finishes.
    initial begin
        #5000000;
        $display("AXI_TEST_FAILED: global timeout");
        $fatal(1, "AXI timeout");
    end

    task automatic reset_all;
        begin
            @(negedge clk);
            rst_n=0; awvalid=0; wvalid=0; bready=0; arvalid=0; rready=0;
            awaddr=0; araddr=0; wdata=0; wstrb=0;
            repeat (4) @(negedge clk);
            rst_n=1;
            repeat (2) @(negedge clk);
        end
    endtask

    task automatic axi_write(
        input [6:0] addr, input [31:0] value, input [3:0] strobe,
        input integer aw_delay, input integer w_delay,
        input integer response_stall, input [1:0] expected_resp
    );
        begin
            @(negedge clk);
            bready=0;
            fork
                begin
                    repeat (aw_delay) @(negedge clk);
                    awaddr=addr; awvalid=1;
                    @(posedge clk);
                    while (awready !== 1'b1) @(posedge clk);
                    @(negedge clk);
                    awvalid=0; awaddr=7'h7c;
                end
                begin
                    repeat (w_delay) @(negedge clk);
                    wdata=value; wstrb=strobe; wvalid=1;
                    @(posedge clk);
                    while (wready !== 1'b1) @(posedge clk);
                    @(negedge clk);
                    wvalid=0; wdata=32'hdeadbeef; wstrb=0;
                end
                begin
                    @(posedge clk);
                    while (bvalid !== 1'b1) @(posedge clk);
                    require(bresp === expected_resp, "write response");
                    repeat (response_stall) begin
                        @(posedge clk);
                        require(bvalid === 1'b1 && bresp === expected_resp,
                                "BVALID/BRESP stable while stalled");
                    end
                    @(negedge clk); bready=1;
                    @(posedge clk);
                    require(bvalid === 1'b1, "write response handshake");
                    @(negedge clk); bready=0;
                end
            join
        end
    endtask

    task automatic wr(input [6:0] addr, input [31:0] value);
        axi_write(addr,value,4'hf,0,0,0,2'b00);
    endtask

    task automatic axi_read(
        input [6:0] addr, input integer response_stall,
        input [1:0] expected_resp, output [31:0] value
    );
        reg [31:0] snapshot;
        begin
            @(negedge clk);
            araddr=addr; arvalid=1; rready=0;
            @(posedge clk);
            while (arready !== 1'b1) @(posedge clk);
            @(negedge clk); arvalid=0; araddr=7'h7c;
            @(posedge clk);
            while (rvalid !== 1'b1) @(posedge clk);
            snapshot=rdata;
            require(rresp === expected_resp, "read response");
            repeat (response_stall) begin
                @(posedge clk);
                require(rvalid === 1'b1 && rresp === expected_resp && rdata === snapshot,
                        "RVALID/RDATA/RRESP stable while stalled");
            end
            value=snapshot;
            @(negedge clk); rready=1;
            @(posedge clk);
            require(rvalid === 1'b1 && rdata === snapshot, "read response handshake");
            @(negedge clk); rready=0;
        end
    endtask

    task automatic rd(input [6:0] addr, output [31:0] value);
        axi_read(addr,0,2'b00,value);
    endtask

    task automatic check_reg(input [6:0] addr, input [31:0] expected,
                             input string label);
        reg [31:0] value;
        begin
            rd(addr,value);
            require(value === expected,label);
        end
    endtask

    task automatic load_identity;
        begin
            wr(7'h10,32'd32768); wr(7'h14,32'd32768);
            wr(7'h18,32'd125829120); wr(7'h1c,0);
            wr(7'h20,0); wr(7'h24,0);
            wr(7'h28,0); wr(7'h2c,32'd70778880);
            wr(7'h30,0); wr(7'h34,0);
            wr(7'h38,1920); wr(7'h3c,1080); wr(7'h40,1);
        end
    endtask

    task automatic wait_done;
        reg [31:0] state;
        integer attempts;
        begin
            state=0; attempts=0;
            while (!state[1] && attempts<40) begin
                rd(7'h04,state); attempts=attempts+1;
            end
            require(state[1] === 1'b1 && state[0] === 1'b0,
                    "DONE visible and BUSY clear");
        end
    endtask

    task automatic expect_result(input [31:0] x, input [31:0] y,
                                  input [31:0] status, input [31:0] tag,
                                  input string label);
        reg [31:0] state;
        begin
            wait_done();
            check_reg(7'h48,x,"result x");
            check_reg(7'h4c,y,"result y");
            check_reg(7'h50,status,"kernel status");
            check_reg(7'h54,tag,"result tag");
            rd(7'h04,state);
            require(state[2] === (status != 0), "state error bit");
            $display("PASS %s: x=%0d y=%0d status=%0d",label,x,y,status);
        end
    endtask

    task automatic run_mapping(
        input [31:0] gx,gy,a0,a1,a2,a3,b0,b1,b2,b3,width,height,valid_in,
        input [31:0] ex,ey,estatus,tag
    );
        begin
            wr(7'h00,32'd2); // acknowledge old result, never silently overwrite
            wr(7'h10,gx); wr(7'h14,gy);
            wr(7'h18,a0); wr(7'h1c,a1); wr(7'h20,a2); wr(7'h24,a3);
            wr(7'h28,b0); wr(7'h2c,b1); wr(7'h30,b2); wr(7'h34,b3);
            wr(7'h38,width); wr(7'h3c,height); wr(7'h40,valid_in); wr(7'h44,tag);
            wr(7'h00,32'd1);
            expect_result(ex,ey,estatus,tag,$sformatf("mapping_tag_%0d",tag));
        end
    endtask

    // Passive response monitors apply to ALL tests, not just helper tasks.
    reg was_b_stalled=0, was_r_stalled=0;
    reg [1:0] previous_bresp, previous_rresp;
    reg [31:0] previous_rdata;
    always @(posedge clk) begin
        if (!rst_n) begin
            was_b_stalled=0; was_r_stalled=0;
        end else begin
            if (was_b_stalled && !(bvalid && bresp === previous_bresp))
                $fatal(1,"AXI_TEST_FAILED: write response changed before acceptance");
            if (was_r_stalled && !(rvalid && rresp === previous_rresp && rdata === previous_rdata))
                $fatal(1,"AXI_TEST_FAILED: read response changed before acceptance");
            was_b_stalled=bvalid && !bready;
            was_r_stalled=rvalid && !rready;
            previous_bresp=bresp; previous_rresp=rresp; previous_rdata=rdata;
        end
    end

    initial begin
        reset_all();
        check_reg(7'h08,32'h475a4d31,"IP identifier");
        check_reg(7'h0c,32'h00010000,"ABI version");
        check_reg(7'h04,0,"reset state");
        check_reg(7'h58,0,"reset completion count");
        check_reg(7'h40,0,"reset input validity is disabled");
        $display("PASS reset_and_identification");

        // Exercise address-before-data, data-before-address, and response stalls.
        axi_write(7'h5c,32'h12345678,4'hf,0,5,7,2'b00);
        check_reg(7'h5c,32'h12345678,"AW before W");
        axi_write(7'h5c,32'h89abcdef,4'hf,6,0,4,2'b00);
        check_reg(7'h5c,32'h89abcdef,"W before AW");
        axi_write(7'h5c,32'h11223344,4'b0101,0,0,0,2'b00);
        check_reg(7'h5c,32'h8922cd44,"byte enables");
        axi_write(7'h5c,32'hffffffff,4'b0000,0,0,0,2'b00);
        check_reg(7'h5c,32'h8922cd44,"zero-strobe write no effect");
        axi_write(7'h00,32'd1,4'b0010,0,0,0,2'b00);
        check_reg(7'h04,0,"masked CTRL does not start");
        axi_write(7'h08,32'hffffffff,4'hf,0,0,0,2'b10);
        check_reg(7'h08,32'h475a4d31,"read-only identifier unchanged");
        axi_write(7'h5d,32'hffffffff,4'hf,0,0,0,2'b10);
        check_reg(7'h5c,32'h8922cd44,"misaligned write no effect");
        axi_read(7'h49,3,2'b10,tmp);
        require(tmp === 32'd0,"misaligned read value");
        check_reg(7'h7c,0,"reserved read zero");
        axi_write(7'h7c,32'd1,4'hf,0,0,0,2'b10);
        $display("PASS independent_channels_strobes_and_addresses");

        // During a stalled read response, modify the underlying register.
        wr(7'h5c,32'h01020304);
        fork
            begin
                axi_read(7'h5c,18,2'b00,held);
                require(held === 32'h01020304,"read snapshot before concurrent write");
            end
            begin
                repeat (5) @(negedge clk);
                wr(7'h5c,32'h55667788);
            end
        join
        check_reg(7'h5c,32'h55667788,"new value visible to next read");
        $display("PASS read_snapshot_under_backpressure");

        load_identity();
        wr(7'h44,32'd101);
        wr(7'h00,32'd1);
        expect_result(960,540,0,101,"center");
        check_reg(7'h58,1,"one completion only");
        check_reg(7'h00,0,"CTRL start pulse is not stored");
        repeat (30) @(negedge clk);
        check_reg(7'h04,2,"DONE remains set for software polling");
        check_reg(7'h58,1,"no automatic retrigger");

        // Unread result protected; rejected start returns SLVERR and sets bit3.
        axi_write(7'h00,1,4'hf,0,0,0,2'b10);
        check_reg(7'h04,10,"unread-result start rejection");
        check_reg(7'h58,1,"rejected start not counted");
        wr(7'h00,8);
        check_reg(7'h04,2,"clear rejection without clearing DONE");
        wr(7'h00,2);
        check_reg(7'h04,0,"ACK clears DONE");

        // A second START commits while the original kernel is still busy.
        wr(7'h00,1);
        axi_write(7'h00,1,4'hf,0,0,0,2'b10);
        expect_result(960,540,0,101,"busy_start_ignored");
        check_reg(7'h58,2,"one result for two START commands while busy");
        wr(7'h00,10); // ACK_DONE | CLEAR_REJECT

        // Stage next coordinates while the first request computes.
        wr(7'h44,32'd102);
        wr(7'h00,1);
        wr(7'h10,32'd16384); // next request gx=0.25, not current gx
        wr(7'h44,32'd103);
        expect_result(960,540,0,102,"input_snapshot");
        wr(7'h00,3); // atomic ACK plus START using the new staged values
        expect_result(480,540,0,103,"ack_and_restart");

        // Deterministic Python-integer reference vectors inserted below.
        run_mapping(32'h00000000, 32'h00000000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h0000012d);
        run_mapping(32'h00010000, 32'h00010000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h0000077f,
            32'h00000437, 32'h00000000, 32'h0000012e);
        run_mapping(32'hffffc000, 32'h00014000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000437, 32'h00000000, 32'h0000012f);
        run_mapping(32'h00008000, 32'h00008000,
            32'h00000000, 32'h00000000, 32'h00000000, 32'h00008000,
            32'h00000000, 32'h00000000, 32'h00000000, 32'h00018000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000001,
            32'h00000002, 32'h00000000, 32'h00000130);
        run_mapping(32'h00005555, 32'h0000aaab,
            32'h00000000, 32'h00000000, 32'h06400000, 32'h00140000,
            32'h00000000, 32'h00000000, 32'h03840000, 32'hfff60000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000178,
            32'h000000be, 32'h00000000, 32'h00000131);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h00000001, 32'h00000132);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000002, 32'h00000000,
            32'h00000000, 32'h00000001, 32'h00000133);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000001, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000001, 32'h00000134);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00002001, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000001, 32'h00000135);
        run_mapping(32'h00014001, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000002, 32'h00000136);
        run_mapping(32'hffffbfff, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000002, 32'h00000137);
        run_mapping(32'h00008000, 32'h00008000,
            32'h75300001, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000003, 32'h00000138);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h00000000, 32'h8acfffff,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000003, 32'h00000139);
        run_mapping(32'h00014000, 32'h00014000,
            32'h75300000, 32'h75300000, 32'h75300000, 32'h75300000,
            32'h8ad00000, 32'h8ad00000, 32'h8ad00000, 32'h8ad00000,
            32'h00002000, 32'h00002000, 32'h00000001, 32'h00001fff,
            32'h00000000, 32'h00000000, 32'h0000013a);
        run_mapping(32'h00000000, 32'h00000000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'h00000002, 32'h00000002, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h0000013b);
        run_mapping(32'h00008000, 32'h00008000,
            32'h07800000, 32'h00000000, 32'h00000000, 32'h00000000,
            32'h00000000, 32'h04380000, 32'h00000000, 32'h00000000,
            32'hffffffff, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000001, 32'h0000013c);
        run_mapping(32'h0000729c, 32'h0000204b,
            32'hfe022a0c, 32'hfaba9009, 32'hfd3010a5, 32'h009f4af3,
            32'hfd3d22cb, 32'h029da1c4, 32'hfe228eaf, 32'h018432c7,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000081, 32'h00000000, 32'h0000013d);
        run_mapping(32'h00008fa9, 32'h0000e167,
            32'h05abb2e1, 32'hff6e703a, 32'hff5fa509, 32'h00903857,
            32'hffbcba0a, 32'hff2b5d1e, 32'h027d3d62, 32'h00471da1,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h000002ef,
            32'h000000a1, 32'h00000000, 32'h0000013e);
        run_mapping(32'h00009958, 32'h00005242,
            32'hfd517fa7, 32'hfa96176b, 32'h02eae260, 32'h05376ccf,
            32'h00e4d66a, 32'hfe75b77b, 32'h0200bc18, 32'h03048759,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h0000026f,
            32'h00000372, 32'h00000000, 32'h0000013f);
        run_mapping(32'h0000f08b, 32'h00009e21,
            32'h0193676c, 32'h02b8d7c3, 32'h05993bbe, 32'h049f9b91,
            32'h00f336d9, 32'h0219ab68, 32'hfde0e889, 32'h0023d17a,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h0000077f,
            32'h00000119, 32'h00000000, 32'h00000140);
        run_mapping(32'h0000ec5d, 32'h0000bcd0,
            32'hfb0aa325, 32'hfe651594, 32'hfd264de7, 32'h04ab08a8,
            32'h0044c192, 32'h02d1783d, 32'h0159f968, 32'h024746c8,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000437, 32'h00000000, 32'h00000141);
        run_mapping(32'h0000cf91, 32'h00006749,
            32'hfd678a32, 32'hfd104ad8, 32'h041b8780, 32'h015cff44,
            32'h028d44fc, 32'hfd5f721d, 32'h02410794, 32'h008d6344,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h0000024c, 32'h00000000, 32'h00000142);
        run_mapping(32'h0000a342, 32'h00013600,
            32'h007af518, 32'h05394af9, 32'h00439147, 32'h005d37c8,
            32'hfe6b0ab1, 32'hfe6d604a, 32'h0299508e, 32'h0029d5f0,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000733,
            32'h00000000, 32'h00000000, 32'h00000143);
        run_mapping(32'h00002068, 32'h0000927d,
            32'hfc1c6c79, 32'h01f1df48, 32'h043fafec, 32'h005d52e5,
            32'h01cc8a5e, 32'h00a734af, 32'hff2a6334, 32'h02af3c27,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h0000014b,
            32'h0000033a, 32'h00000000, 32'h00000144);
        run_mapping(32'h0000a82d, 32'h0000d2f7,
            32'hfc477a9e, 32'hfac2ed45, 32'hfd18310b, 32'h028c538a,
            32'hffeea57e, 32'hfcf0dd32, 32'hfce62425, 32'h00812c57,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h00000145);
        run_mapping(32'h0000cb74, 32'h000084af,
            32'hfddb9be4, 32'hfcaa71c1, 32'h00547723, 32'h0115b58c,
            32'hfe53a025, 32'hfdd6f275, 32'hffa31db4, 32'h00a1aea6,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h00000146);
        run_mapping(32'h000032dc, 32'hffffc53c,
            32'h03a02a6f, 32'hfe5ffe19, 32'h0047f57b, 32'h0221c4c3,
            32'hfe16d715, 32'hfd0986ba, 32'h0309ce26, 32'h00bbf64f,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000336,
            32'h000000e5, 32'h00000000, 32'h00000147);
        run_mapping(32'h000064b9, 32'h00004d8f,
            32'h04b97c8b, 32'h03706de3, 32'h045e98d9, 32'h013da29e,
            32'hffa9058a, 32'hfdc4cea6, 32'h02a2547c, 32'h02213ec0,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h000004aa,
            32'h000001a2, 32'h00000000, 32'h00000148);
        run_mapping(32'h00011c18, 32'hfffff7b9,
            32'h016481d1, 32'h01b0eb4a, 32'h032b55d5, 32'h04503eba,
            32'hfefcc665, 32'h01f2f20d, 32'hfd78cb47, 32'h00d36eaa,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h000005b1,
            32'h00000000, 32'h00000000, 32'h00000149);
        run_mapping(32'h00005054, 32'h0000da65,
            32'h0149d6ba, 32'h0522d442, 32'h0365dcbf, 32'h033a9997,
            32'h00c00313, 32'hfdec2cfb, 32'h0234363d, 32'h023a5e1c,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h0000077f,
            32'h00000148, 32'h00000000, 32'h0000014a);
        run_mapping(32'h0000a23f, 32'h00001713,
            32'hfeb1ef33, 32'hfd350cb6, 32'h04429caf, 32'h008460ee,
            32'hfef52022, 32'h027b4b76, 32'h02106ed4, 32'h0037fd1d,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h00000000,
            32'h00000000, 32'h00000000, 32'h0000014b);
        run_mapping(32'h0000ec47, 32'hffffdbb3,
            32'h02438f92, 32'hfeb0f3c3, 32'h038ea968, 32'h03d3fb71,
            32'h0202368f, 32'hff76036b, 32'hfdb08508, 32'h02c0a96d,
            32'h00000780, 32'h00000438, 32'h00000001, 32'h000005a3,
            32'h00000437, 32'h00000000, 32'h0000014c);

        // Abort a running calculation: clear output state, retain configuration.
        wr(7'h00,10);
        load_identity(); wr(7'h44,201);
        wr(7'h00,1);
        wr(7'h00,4);
        repeat (20) @(negedge clk);
        check_reg(7'h04,0,"soft abort clears state");
        check_reg(7'h48,0,"soft abort clears result");
        check_reg(7'h58,0,"soft abort clears completion counter");
        check_reg(7'h38,1920,"soft abort retains staging configuration");
        wr(7'h00,1);
        expect_result(960,540,0,201,"recovery_after_soft_abort");

        // Hard reset cancels an AW without its W. A subsequent W alone must
        // wait for a NEW address, not reuse the pre-reset address.
        reset_all();
        @(negedge clk); awaddr=7'h10; awvalid=1;
        @(posedge clk); while (!awready) @(posedge clk);
        @(negedge clk); awvalid=0;
        reset_all();
        @(negedge clk); wdata=32'habcde012; wstrb=4'hf; wvalid=1;
        @(posedge clk); while (!wready) @(posedge clk);
        @(negedge clk); wvalid=0;
        repeat (4) begin
            @(posedge clk); require(!bvalid,"reset discarded unmatched AW");
        end
        @(negedge clk); awaddr=7'h5c; awvalid=1;
        @(posedge clk); while (!awready) @(posedge clk);
        @(negedge clk); awvalid=0;
        @(posedge clk); while (!bvalid) @(posedge clk);
        require(bresp===2'b00,"post-reset AW/W pair accepted");
        @(negedge clk); bready=1;
        @(posedge clk);
        @(negedge clk); bready=0;
        check_reg(7'h5c,32'habcde012,"data paired with new address");
        check_reg(7'h10,0,"old address not written");

        // Mirror test: reset discards an unmatched W.
        reset_all();
        @(negedge clk); wdata=32'h11112222; wstrb=4'hf; wvalid=1;
        @(posedge clk); while (!wready) @(posedge clk);
        @(negedge clk); wvalid=0;
        reset_all();
        @(negedge clk); awaddr=7'h5c; awvalid=1;
        @(posedge clk); while (!awready) @(posedge clk);
        @(negedge clk); awvalid=0;
        repeat (4) begin
            @(posedge clk); require(!bvalid,"reset discarded unmatched W");
        end
        @(negedge clk); wdata=32'h33334444; wstrb=4'hf; wvalid=1;
        @(posedge clk); while (!wready) @(posedge clk);
        @(negedge clk); wvalid=0;
        @(posedge clk); while (!bvalid) @(posedge clk);
        require(bresp===2'b00,"new W completes post-reset address");
        @(negedge clk); bready=1;
        @(posedge clk);
        @(negedge clk); bready=0;
        check_reg(7'h5c,32'h33334444,"old write data not reused");
        $display("PASS reset_of_unmatched_write_channels");

        // Backpressure: do not accept a second write while the first B response
        // is pending. Master holds its next AW/W until READY becomes true.
        @(negedge clk);
        awaddr=7'h5c; awvalid=1; wdata=32'h11111111; wstrb=4'hf; wvalid=1;
        @(posedge clk); require(awready && wready,"initial write accepted");
        @(negedge clk); awvalid=0; wvalid=0;
        @(posedge clk); while (!bvalid) @(posedge clk);
        @(negedge clk);
        awaddr=7'h5c; awvalid=1; wdata=32'h22222222; wvalid=1;
        repeat (6) begin
            @(posedge clk);
            require(bvalid && !awready && !wready,"next write blocked behind response");
        end
        @(negedge clk); bready=1;
        @(posedge clk); require(bvalid && bresp===0,"first response accepted");
        @(negedge clk); bready=0;
        @(posedge clk);
        while (!(awready && wready)) @(posedge clk);
        @(negedge clk); awvalid=0; wvalid=0;
        @(posedge clk); while (!bvalid) @(posedge clk);
        require(bresp===0,"second response issued");
        @(negedge clk); bready=1;
        @(posedge clk);
        @(negedge clk); bready=0;
        check_reg(7'h5c,32'h22222222,"second pending write committed exactly once");
        $display("PASS consecutive_writes_with_response_backpressure");

        // Reset cancels an unaccepted read response.
        @(negedge clk); araddr=7'h5c; arvalid=1; rready=0;
        @(posedge clk); while (!arready) @(posedge clk);
        @(negedge clk); arvalid=0;
        @(posedge clk); while (!rvalid) @(posedge clk);
        reset_all();
        require(!rvalid && !bvalid,"reset clears pending responses");
        check_reg(7'h5c,0,"hard reset clears scratch");

        // Reset while a START write response is pending also cancels the job.
        load_identity(); wr(7'h44,901);
        @(negedge clk);
        awaddr=0; awvalid=1; wdata=1; wstrb=4'hf; wvalid=1; bready=0;
        @(posedge clk); require(awready && wready,"START accepted before reset");
        @(negedge clk); awvalid=0; wvalid=0;
        @(posedge clk); while (!bvalid) @(posedge clk);
        reset_all();
        repeat (15) @(negedge clk);
        require(!bvalid && !rvalid,"no stale response after reset");
        check_reg(7'h04,0,"reset cancels active job");
        check_reg(7'h58,0,"canceled job not completed");
        load_identity(); wr(7'h44,902); wr(7'h00,1);
        expect_result(960,540,0,902,"recovery_after_hard_reset");
        $display("PASS reset_pending_responses_and_inflight_job");

        $display("----------------------------------------------");
        $display("AXI_TESTS_PASSED: %0d checks",checks);
        $display("This is behavioral simulation, not board validation.");
        $display("----------------------------------------------");
        $finish;
    end
endmodule
