set root [file normalize [file dirname [info script]]]
create_project hardware_interaction [file join $root project_v2] -part xc7z020clg400-1
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file join $root baseline ip_repo]] [current_project]
update_ip_catalog
source [file join $root baseline reports rebuilt_bd.tcl]
validate_bd_design
save_bd_design
file mkdir [file join $root reports] [file join $root artifacts]
write_bd_tcl -force [file join $root reports hardware_interaction_bd.tcl]
set bd [get_files */eye_muse_bd.bd]
generate_target all $bd
set wrapper [make_wrapper -files $bd -top]
add_files -norecurse $wrapper
set_property top eye_muse_bd_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
open_run synth_1
read_xdc [file join $root baseline constraints uart_pins.xdc]
write_checkpoint -force [file join $root artifacts hardware_interaction_synth.dcp]
opt_design
place_design -directive Explore
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
phys_opt_design -directive AggressiveExplore
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $root reports timing.rpt]
report_utilization -file [file join $root reports utilization.rpt]
report_drc -file [file join $root reports drc.rpt]
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set f [open [file join $root reports result.txt] w];puts $f "WNS=$wns WHS=$whs";close $f
write_checkpoint -force [file join $root artifacts hardware_interaction_routed.dcp]
if {$wns<0 || $whs<0} {error "Timing failed: WNS=$wns WHS=$whs"}
write_bitstream -force [file join $root artifacts hardware_interaction.bit]
set hwh [lindex [glob -nocomplain [file join $root project_v2 hardware_interaction.gen sources_1 bd eye_muse_bd hw_handoff *.hwh]] 0]
if {$hwh eq ""} {error "HWH missing"}
file copy -force $hwh [file join $root artifacts hardware_interaction.hwh]
puts HARDWARE_INTERACTION_BUILD_PASS
