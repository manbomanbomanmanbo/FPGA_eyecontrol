# Fixed-width customization contract for gaze_map_axi 1.0.
proc init_gui {IPINST} {
    ipgui::add_param $IPINST -name Component_Name
    set page [ipgui::add_page $IPINST -name Configuration]
    foreach name {C_S00_AXI_DATA_WIDTH C_S00_AXI_ADDR_WIDTH C_S00_AXI_BASEADDR C_S00_AXI_HIGHADDR} {
        ipgui::add_param $IPINST -name $name -parent $page
    }
}
proc validate_PARAM_VALUE.C_S00_AXI_DATA_WIDTH {PARAM_VALUE.C_S00_AXI_DATA_WIDTH} {
    if {[get_property value ${PARAM_VALUE.C_S00_AXI_DATA_WIDTH}] != 32} {
        set_property errmsg {This IP version supports only DATA_WIDTH=32} ${PARAM_VALUE.C_S00_AXI_DATA_WIDTH}
        return false
    }
    return true
}
proc validate_PARAM_VALUE.C_S00_AXI_ADDR_WIDTH {PARAM_VALUE.C_S00_AXI_ADDR_WIDTH} {
    if {[get_property value ${PARAM_VALUE.C_S00_AXI_ADDR_WIDTH}] != 7} {
        set_property errmsg {This IP version supports only ADDR_WIDTH=7} ${PARAM_VALUE.C_S00_AXI_ADDR_WIDTH}
        return false
    }
    return true
}
proc update_MODELPARAM_VALUE.C_S00_AXI_DATA_WIDTH {MODELPARAM_VALUE.C_S00_AXI_DATA_WIDTH PARAM_VALUE.C_S00_AXI_DATA_WIDTH} {
    set_property value [get_property value ${PARAM_VALUE.C_S00_AXI_DATA_WIDTH}] ${MODELPARAM_VALUE.C_S00_AXI_DATA_WIDTH}
}
proc update_MODELPARAM_VALUE.C_S00_AXI_ADDR_WIDTH {MODELPARAM_VALUE.C_S00_AXI_ADDR_WIDTH PARAM_VALUE.C_S00_AXI_ADDR_WIDTH} {
    set_property value [get_property value ${PARAM_VALUE.C_S00_AXI_ADDR_WIDTH}] ${MODELPARAM_VALUE.C_S00_AXI_ADDR_WIDTH}
}

