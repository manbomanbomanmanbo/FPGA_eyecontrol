"""Windows 图标的默认打开动作，仅用于眨眼双击。

通过系统辅助功能接口调用鼠标下文件/快捷方式的默认动作；
不向当前活动窗口发送 Enter，也不按文件名猜测打开路径。
"""

import ctypes
from ctypes import wintypes
import base64
import json
import os
from pathlib import Path
import subprocess


_DESKTOP_ITEM_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$taskUtf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $taskUtf8
[Console]::OutputEncoding = $taskUtf8
try {
    $taskRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (($taskRequest.name -isnot [string]) -or
        [string]::IsNullOrEmpty($taskRequest.name) -or
        ($taskRequest.invoke -isnot [bool])) {
        throw 'Invalid desktop item request'
    }
    $taskShell = New-Object -ComObject Shell.Application
    $taskDesktop = $taskShell.Namespace(0)
    if ($null -eq $taskDesktop) { throw 'Desktop Shell namespace unavailable' }
    $taskItems = $taskDesktop.Items()
    $taskMatchedItems = @()
    for ($taskIndex = 0; $taskIndex -lt $taskItems.Count; $taskIndex++) {
        $taskItem = $taskItems.Item($taskIndex)
        if ([string]::Equals([string]$taskItem.Name, $taskRequest.name,
                             [System.StringComparison]::Ordinal)) {
            $taskMatchedItems += $taskItem
        }
    }
    if ($taskMatchedItems.Count -ne 1) {
        throw ('Desktop item match is not unique: ' + $taskMatchedItems.Count)
    }
    $taskTarget = $taskMatchedItems[0]
    $taskResult = [ordered]@{
        name = [string]$taskTarget.Name
        path = [string]$taskTarget.Path
        is_folder = [bool]$taskTarget.IsFolder
        is_link = [bool]$taskTarget.IsLink
        method = 'shell_folder_item'
        invoked = $taskRequest.invoke
    }
    if ([string]::IsNullOrEmpty($taskResult.path)) { throw 'Desktop item has no Shell path' }
    if ($taskRequest.invoke) { $taskTarget.InvokeVerb() }
    [Console]::WriteLine(($taskResult | ConvertTo-Json -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def desktop_shell_item(name, invoke=True):
    """Resolve one exact desktop Shell item; invoke=False performs no action."""
    if not isinstance(name, str) or not name or type(invoke) is not bool:
        raise ValueError("Invalid desktop Shell item request")
    powershell = (Path(os.environ.get("SystemRoot", r"C:\Windows"))
                  / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    encoded = base64.b64encode(_DESKTOP_ITEM_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Sta",
             "-EncodedCommand", encoded],
            input=json.dumps({"name": name, "invoke": invoke}, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8", timeout=3.0,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        # A timed-out invocation may already be in progress: never add clicks.
        raise OSError("Desktop Shell action timed out; result is unknown") from error
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "Desktop Shell item resolution failed")
    item = json.loads(result.stdout.lstrip("\ufeff"))
    if (not isinstance(item, dict) or item.get("name") != name
            or item.get("method") != "shell_folder_item" or not item.get("path")
            or item.get("invoked") is not invoke):
        raise OSError("Unexpected desktop Shell item response")
    return item


class _VariantData(ctypes.Union):
    _fields_ = [("lVal", wintypes.LONG), ("pointer", ctypes.c_void_p),
                ("record", ctypes.c_void_p * 2)]


class _Variant(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("vt", wintypes.WORD), ("reserved1", wintypes.WORD),
                ("reserved2", wintypes.WORD), ("reserved3", wintypes.WORD),
                ("data", _VariantData)]


class _Guid(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]


_IID_IACCESSIBLE = _Guid(0x618736E0, 0x3C3D, 0x11CF,
                        (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA,
                                            0x00, 0x38, 0x9B, 0x71))


def _method(interface, slot, *argtypes):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.c_void_p, *argtypes)(vtable[slot])


def _check(hr):
    if hr < 0:
        raise OSError(f"Windows 图标接口错误：0x{hr & 0xffffffff:08X}")


def open_shell_icon(position):
    """返回 None 表示非可打开图标；成功返回图标信息；失败抛异常。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # 主程序导入 pyautogui 后使用物理屏幕坐标。独立 helper 必须采用
    # 同样的 DPI 模式，否则 125% 缩放下会把屏幕下方位置判为屏幕外。
    user32.SetProcessDPIAware.argtypes = ()
    user32.SetProcessDPIAware.restype = wintypes.BOOL
    if not user32.SetProcessDPIAware():
        raise ctypes.WinError(ctypes.get_last_error())
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    point = wintypes.POINT(*map(int, position))
    window = user32.WindowFromPoint(point)
    root = user32.GetAncestor(window, 2)
    classname = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(root, classname, len(classname))
    # 只处理桌面和资源管理器；工具栏、其他软件仍使用普通双击。
    if classname.value not in ("Progman", "WorkerW", "CabinetWClass"):
        return None

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoInitializeEx.restype = wintypes.LONG
    ole32.CoUninitialize.argtypes = ()
    oleacc = ctypes.WinDLL("oleacc")
    oleacc.AccessibleObjectFromPoint.argtypes = (
        wintypes.POINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(_Variant))
    oleacc.AccessibleObjectFromPoint.restype = wintypes.LONG
    oleacc.WindowFromAccessibleObject.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.HWND))
    oleacc.WindowFromAccessibleObject.restype = wintypes.LONG
    oleaut32 = ctypes.WinDLL("oleaut32")
    oleaut32.SysFreeString.argtypes = (ctypes.c_void_p,)
    oleaut32.VariantClear.argtypes = (ctypes.POINTER(_Variant),)

    initialized = ole32.CoInitializeEx(None, 2)
    # 已被其他库初始化为另一线程模式时，复用已有 COM 环境。
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE
        _check(initialized)
    accessible = ctypes.c_void_p()
    original_accessible = accessible
    parent_interfaces = []
    child = _Variant()
    role = _Variant()
    state = _Variant()
    try:
        _check(oleacc.AccessibleObjectFromPoint(point, ctypes.byref(accessible),
                                              ctypes.byref(child)))
        if not accessible or child.vt != 3:  # VT_I4 / CHILDID_SELF
            return None
        _check(_method(accessible, 13, _Variant, ctypes.POINTER(_Variant))(
            accessible, child, ctypes.byref(role)))
        if role.vt == 3 and role.lVal == 42:  # ROLE_SYSTEM_TEXT: icon label/editor
            if child.lVal != 0:
                # A simple child's parent is the current IAccessible itself.
                child.lVal = 0  # CHILDID_SELF; never reuse the old child index.
            else:
                # A full text object's direct parent is returned as IDispatch.
                # QueryInterface before accessing IAccessible-specific slots.
                dispatch = ctypes.c_void_p()
                parent = ctypes.c_void_p()
                try:
                    _check(_method(accessible, 7, ctypes.POINTER(ctypes.c_void_p))(
                        accessible, ctypes.byref(dispatch)))
                    if not dispatch:
                        return None
                    _check(_method(dispatch, 0, ctypes.POINTER(_Guid),
                                   ctypes.POINTER(ctypes.c_void_p))(
                        dispatch, ctypes.byref(_IID_IACCESSIBLE), ctypes.byref(parent)))
                finally:
                    if parent:
                        parent_interfaces.append(parent)
                    if dispatch:
                        _method(dispatch, 2)(dispatch)
                if not parent:
                    return None
                accessible = parent
                child.lVal = 0
            # Only text -> direct ListItem is allowed; do not climb a list/window
            # or choose a previously selected item when no icon contains the point.
            oleaut32.VariantClear(ctypes.byref(role))
            _check(_method(accessible, 13, _Variant, ctypes.POINTER(_Variant))(
                accessible, child, ctypes.byref(role)))
        if role.vt != 3 or role.lVal != 34:  # ROLE_SYSTEM_LISTITEM
            return None
        owner = wintypes.HWND()
        _check(oleacc.WindowFromAccessibleObject(accessible, ctypes.byref(owner)))
        if not owner or user32.GetAncestor(owner, 2) != root:
            return None
        _check(_method(accessible, 14, _Variant, ctypes.POINTER(_Variant))(
            accessible, child, ctypes.byref(state)))
        if state.vt != 3 or state.lVal & (0x1 | 0x8000 | 0x10000):
            return None  # unavailable / invisible / offscreen

        def read_string(slot):
            value = ctypes.c_void_p()
            try:
                _check(_method(accessible, slot, _Variant, ctypes.POINTER(ctypes.c_void_p))(
                    accessible, child, ctypes.byref(value)))
                return ctypes.wstring_at(value) if value else ""
            finally:
                if value:
                    oleaut32.SysFreeString(value)

        name = read_string(10)
        action = read_string(20)
        if not name or not action:
            return None
        # 检查该项的实际矩形，避免列表空白处命中之前选中的图标。
        left, top, width, height = (wintypes.LONG() for _ in range(4))
        _check(_method(accessible, 22, ctypes.POINTER(wintypes.LONG),
                       ctypes.POINTER(wintypes.LONG), ctypes.POINTER(wintypes.LONG),
                       ctypes.POINTER(wintypes.LONG), _Variant)(
            accessible, ctypes.byref(left), ctypes.byref(top),
            ctypes.byref(width), ctypes.byref(height), child))
        if not (left.value <= point.x < left.value + width.value
                and top.value <= point.y < top.value + height.value):
            return None
        if user32.GetAncestor(user32.WindowFromPoint(point), 2) != root:
            return None
        info = {"name": name, "action": action, "root": root,
                "class": classname.value, "position": [point.x, point.y]}
        if classname.value in ("Progman", "WorkerW"):
            # MSAA only establishes the exact hit. Use the real Shell item to
            # execute its default verb without depending on focus/selection.
            return {**info, **desktop_shell_item(name, invoke=True)}
        result = _method(accessible, 25, _Variant)(accessible, child)
        _check(result)
        if result != 0:  # 只有 S_OK 表示接受了默认动作请求。
            raise OSError(f"Windows 未接受图标打开动作（HRESULT={result}）")
        return {**info, "method": "msaa_default_action"}
    finally:
        for value in (state, role, child):
            oleaut32.VariantClear(ctypes.byref(value))
        for interface in reversed(parent_interfaces):
            _method(interface, 2)(interface)
        if original_accessible:
            _method(original_accessible, 2)(original_accessible)
        if initialized >= 0:
            ole32.CoUninitialize()


if __name__ == "__main__":
    import sys

    try:
        result = open_shell_icon((int(sys.argv[1]), int(sys.argv[2])))
        # ensure_ascii 避免 Windows 控制台编码影响父进程读取。
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        sys.stderr.buffer.write(str(error).encode("utf-8", errors="replace"))
        sys.exit(1)
