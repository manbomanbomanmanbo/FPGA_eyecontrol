"""Measured laser-head offsets; importing this module never opens hardware or UI."""
import json
import math
from pathlib import Path


def _valid_offset(values):
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        raise ValueError("激光安装偏移必须包含 X、Y、Z 三个数值")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in values):
        raise ValueError("激光安装偏移必须是有限数值")
    return tuple(float(value) for value in values)


def load_laser_offset(config_path, *, require_measurement=False):
    """Return a measured offset in metres / head Y-up, or None if absent."""
    path = Path(config_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("model") != "aligned_pan_tilt_laser_v1":
            raise ValueError("激光安装文件的模型标识无效")
        if require_measurement and data.get("measurement_confirmed") is not True:
            return None
        millimetres = _valid_offset(data.get("laser_offset_mm"))
        return tuple(value / 1000.0 for value in millimetres)
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("激光安装文件格式无效") from error


def save_laser_offset(config_path, laser_offset_metres):
    values = _valid_offset(laser_offset_metres)
    millimetres = _valid_offset([value * 1000.0 for value in values])
    data = {"model": "aligned_pan_tilt_laser_v1", "laser_offset_mm": millimetres,
            "measurement_confirmed": True}
    Path(config_path).write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                           allow_nan=False) + "\n", encoding="utf-8")


def request_laser_offset(config_path, current_offset=None, *, require_measurement=False):
    """Ask for ruler measurements; cancel keeps the existing approximate mode."""
    import tkinter as tk
    from tkinter import messagebox

    initial = None if current_offset is None else _valid_offset(current_offset)
    root = tk.Tk()
    root.title("激光安装位置补偿")
    root.resizable(False, False)
    result = {"offset": None}
    tk.Label(root, text="激光孔相对两转轴交点的位置", font=("Microsoft YaHei", 15, "bold")).pack(padx=20, pady=(15, 8))
    tk.Label(root, text="先让云台保持水平、激光朝前，再用尺测量（单位：mm）。\n"
             "这里测激光孔的位置；前一个窗口测的是转轴交点相对相机的位置。",
             justify="left").pack(padx=20, pady=(0, 12))
    form = tk.Frame(root)
    form.pack(padx=20)
    entries = []
    for index, (label, hint) in enumerate((
            ("X 左右偏移", "右正、左负；居中才填 0"),
            ("Y 安装高度", "光孔高于上下转轴为正"),
            ("Z 前后偏移", "光孔在交点前方为正"))):
        tk.Label(form, text=label).grid(row=index, column=0, sticky="e", pady=7)
        entry = tk.Entry(form, width=15)
        entry.grid(row=index, column=1, padx=10)
        tk.Label(form, text=hint).grid(row=index, column=2, sticky="w")
        if initial is not None:
            entry.insert(0, f"{initial[index] * 1000.0:g}")
        elif index != 1 and not require_measurement:
            entry.insert(0, "0")
        entries.append(entry)

    def confirm():
        try:
            millimetres = _valid_offset([float(entry.get()) for entry in entries])
            offset = tuple(value / 1000.0 for value in millimetres)
            save_laser_offset(config_path, offset)
        except (ValueError, OSError) as error:
            messagebox.showerror("安装参数未保存", str(error), parent=root)
            return
        result["offset"] = offset
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(pady=16)
    tk.Button(buttons, text="保存实测值", command=confirm).pack(side="left", padx=8)
    tk.Button(buttons, text="稍后测量" if require_measurement else "稍后测量（暂不补偿）",
              command=root.destroy).pack(side="left", padx=8)
    root.bind("<Return>", lambda event: confirm())
    root.bind("<Escape>", lambda event: root.destroy())
    entries[1].focus_set()
    root.mainloop()
    return result["offset"]
