# PYNQ-Z2 PMODB: physical1=W14 (TX), physical2=Y14 (RX), LVCMOS33.
# Official: Xilinx/PYNQ v3.0.1 boards/Pynq-Z2/base/vivado/constraints/base.xdc
# UART is asynchronous. Cut ONLY pad -> first synchronizer D. Preserve the
# rx_meta -> rx_sync 100MHz path. Delays0 are a fabric-clock accounting reference,
# not a claim of controller interface setup/hold. TX registered I/O remains timed.
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33 PULLTYPE NONE} [get_ports uart_tx]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33 PULLTYPE NONE} [get_ports uart_rx]
set_input_delay -clock [get_clocks clk_fpga_0] -min 0.000 [get_ports uart_rx]
set_input_delay -clock [get_clocks clk_fpga_0] -max 0.000 [get_ports uart_rx]
set_output_delay -clock [get_clocks clk_fpga_0] -min 0.000 [get_ports uart_tx]
set_output_delay -clock [get_clocks clk_fpga_0] -max 0.000 [get_ports uart_tx]
set rx_meta_d [get_pins -hier -filter {NAME =~ */rx_meta_reg/D}]
set_false_path -from [get_ports uart_rx] -to $rx_meta_d
