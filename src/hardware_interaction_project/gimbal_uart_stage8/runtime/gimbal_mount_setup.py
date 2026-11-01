"""Measured camera-to-gimbal origin, with no assumed installation coordinates."""
import json
import math
import os
from pathlib import Path
import tempfile


def valid_origin(values):
    if (type(values) not in (list, tuple) or len(values) != 3
            or any(isinstance(value, bool) or not isinstance(value, (float, int))
                   or not math.isfinite(value) for value in values)):
        raise ValueError("转轴交点位置需要三个有限数值，单位为 mm")
    return tuple(float(value) for value in values)


def load_origin(path):
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (type(data) is not dict or data.get("model") != "zed_image_gimbal_origin_v1"
            or data.get("measurement_confirmed") is not True):
        raise ValueError("安装位置文件缺少实测确认，不能使用")
    return valid_origin(data.get("rotation_centre_mm"))


def save_origin(path, values):
    values = valid_origin(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"model": "zed_image_gimbal_origin_v1", "measurement_confirmed": True,
                       "axes": "X right, Y down, Z forward", "rotation_centre_mm": values},
                      stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def request_origin(path, previous=None):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("云台相对相机的安装位置")
    root.resizable(False, False)
    tk.Label(root, text="测量两转轴交点相对 ZED 相机的位置", font=("Microsoft YaHei", 13, "bold")).pack(padx=18, pady=14)
    tk.Label(root, text="单位：mm；相机坐标 X 向右、Y 向下、Z 向前。\n"
             "这里是安装位置，不是电机角度零点；未知时先取消测量。", justify="left").pack(padx=18)
    frame = tk.Frame(root)
    frame.pack(padx=18, pady=14)
    entries = []
    for index, text in enumerate(("X（右正、左负）", "Y（下正、上负）", "Z（前正、后负）")):
        tk.Label(frame, text=text).grid(row=index, column=0, pady=8, sticky="e")
        entry = tk.Entry(frame, width=18)
        entry.grid(row=index, column=1, padx=12)
        if previous is not None:
            entry.insert(0, "%g" % previous[index])
        entries.append(entry)
    result = {"origin": None}

    def confirm():
        try:
            values = valid_origin([float(entry.get()) for entry in entries])
            save_origin(path, values)
        except (ValueError, OSError) as error:
            messagebox.showerror("尚未保存安装位置", str(error), parent=root)
            return
        result["origin"] = values
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(pady=(0, 16))
    tk.Button(buttons, text="保存实测值", command=confirm).pack(side="left", padx=8)
    tk.Button(buttons, text="稍后测量", command=root.destroy).pack(side="left", padx=8)
    root.bind("<Return>", lambda event: confirm())
    root.bind("<Escape>", lambda event: root.destroy())
    entries[0].focus_set()
    root.mainloop()
    return result["origin"]
