# import sys
# import json
# import socket
# import threading
# import time
# import math
# import cv2
# import numpy as np
# import pyzed.sl as zed
#
#
# class ZEDServer:
#
#     def __init__(self, port=9999):
#
#         self.port = port
#
#         # =====================================================
#         # ZED
#         # =====================================================
#
#         self.cam = None
#
#         # 官方点云
#         self.point_cloud = zed.Mat()
#
#         # 官方深度图
#         self.depth_image = zed.Mat()
#
#         # =====================================================
#         # ZED 原始图像尺寸
#         # =====================================================
#
#         self.orig_w = 1280
#         self.orig_h = 720
#
#         # =====================================================
#         # 深度窗口
#         # =====================================================
#
#         self.window_name = "ZED Environment"
#
#         self.window_visible = False
#         self.window_created = False
#
#         # =====================================================
#         # 窗口固定位置
#         #
#         # 这里会根据电脑屏幕自动计算居中位置
#         # =====================================================
#
#         self.window_x = 0
#         self.window_y = 0
#
#         # =====================================================
#         # 运行状态
#         # =====================================================
#
#         self.running = True
#
#         # =====================================================
#         # 坐标显示
#         # =====================================================
#
#         self.display_text = ""
#         self.display_text_expire = 0.0
#
#
#     # =========================================================
#     # 初始化 ZED
#     # =========================================================
#
#     def init_camera(self):
#
#         cam = zed.Camera()
#
#         init = zed.InitParameters()
#
#         # -----------------------------------------------------
#         # 完全按照官方示例
#         # -----------------------------------------------------
#
#         init.camera_resolution = zed.RESOLUTION.HD720
#
#         init.depth_mode = zed.DEPTH_MODE.ULTRA
#
#         init.coordinate_units = zed.UNIT.METER
#
#         init.depth_maximum_distance = 40
#
#         init.camera_fps = 30
#
#         # -----------------------------------------------------
#         # 打开相机
#         # -----------------------------------------------------
#
#         err = cam.open(init)
#
#         if err != zed.ERROR_CODE.SUCCESS:
#
#             print(
#                 f"ZED 相机打开失败: {err}"
#             )
#
#             return False
#
#         self.cam = cam
#
#         # -----------------------------------------------------
#         # 获取真实分辨率
#         # -----------------------------------------------------
#
#         camera_info = cam.get_camera_information()
#
#         self.orig_w = (
#             camera_info
#             .camera_configuration
#             .resolution
#             .width
#         )
#
#         self.orig_h = (
#             camera_info
#             .camera_configuration
#             .resolution
#             .height
#         )
#
#         print(
#             "Camera initialized successfully"
#         )
#
#         print(
#             f"ZED resolution: "
#             f"{self.orig_w} x {self.orig_h}"
#         )
#
#         # -----------------------------------------------------
#         # 获取电脑屏幕尺寸
#         # -----------------------------------------------------
#
#         try:
#
#             import pyautogui
#
#             screen_w, screen_h = pyautogui.size()
#
#         except Exception as e:
#
#             print(
#                 f"获取屏幕尺寸失败: {e}"
#             )
#
#             return False
#
#         # -----------------------------------------------------
#         # 窗口居中
#         # -----------------------------------------------------
#
#         self.window_x = int(
#             (screen_w - self.orig_w) / 2
#         )
#
#         self.window_y = int(
#             (screen_h - self.orig_h) / 2
#         )
#
#         # 防止屏幕比窗口小
#         self.window_x = max(
#             0,
#             self.window_x
#         )
#
#         self.window_y = max(
#             0,
#             self.window_y
#         )
#
#         print(
#             f"Screen size: "
#             f"{screen_w} x {screen_h}"
#         )
#
#         print(
#             f"ZED window position: "
#             f"({self.window_x}, "
#             f"{self.window_y})"
#         )
#
#         print(
#             f"ZED window size: "
#             f"{self.orig_w} x "
#             f"{self.orig_h}"
#         )
#
#         return True
#
#
#     # =========================================================
#     # 屏幕坐标 -> ZED图像坐标
#     #
#     # 电脑鼠标坐标：
#     #
#     #     Windows整个屏幕坐标
#     #
#     # ZED坐标：
#     #
#     #     1280 x 720 深度图坐标
#     #
#     # 只有鼠标在窗口内部才允许转换
#     # =========================================================
#
#     def screen_to_image_coord(
#         self,
#         screen_x,
#         screen_y
#     ):
#
#         try:
#
#             screen_x = float(screen_x)
#             screen_y = float(screen_y)
#
#         except Exception:
#
#             return -1, -1
#
#         # =====================================================
#         # 1. 判断鼠标是否在 ZED 窗口内部
#         # =====================================================
#
#         left = self.window_x
#         top = self.window_y
#
#         right = (
#             self.window_x +
#             self.orig_w
#         )
#
#         bottom = (
#             self.window_y +
#             self.orig_h
#         )
#
#         # -----------------------------------------------------
#         # 窗口外
#         # -----------------------------------------------------
#
#         if screen_x < left:
#             return -1, -1
#
#         if screen_x >= right:
#             return -1, -1
#
#         if screen_y < top:
#             return -1, -1
#
#         if screen_y >= bottom:
#             return -1, -1
#
#         # =====================================================
#         # 2. 屏幕坐标转换成窗口内部坐标
#         # =====================================================
#
#         ix = int(
#             screen_x -
#             self.window_x
#         )
#
#         iy = int(
#             screen_y -
#             self.window_y
#         )
#
#         # =====================================================
#         # 3. 最终边界保护
#         # =====================================================
#
#         if ix < 0:
#             return -1, -1
#
#         if iy < 0:
#             return -1, -1
#
#         if ix >= self.orig_w:
#             return -1, -1
#
#         if iy >= self.orig_h:
#             return -1, -1
#
#         return ix, iy
#
#
#     # =========================================================
#     # 官方 ZED 点云查询
#     #
#     # 核心就是：
#     #
#     #     point_cloud.get_value(x, y)
#     #
#     # 和你的官方示例一致
#     # =========================================================
#
#     def get_world_coord(
#         self,
#         screen_x,
#         screen_y
#     ):
#
#         if self.cam is None:
#             return None
#
#         if not self.window_visible:
#             return None
#
#         # =====================================================
#         # 屏幕坐标 -> ZED图像坐标
#         # =====================================================
#
#         ix, iy = self.screen_to_image_coord(
#             screen_x,
#             screen_y
#         )
#
#         # -----------------------------------------------------
#         # 鼠标不在深度图区域
#         # -----------------------------------------------------
#
#         if ix < 0 or iy < 0:
#
#             return None
#
#         try:
#
#             # =================================================
#             # 官方示例的核心
#             # =================================================
#
#             err, point3D = (
#                 self.point_cloud.get_value(
#                     ix,
#                     iy
#                 )
#             )
#
#             # -------------------------------------------------
#             # ZED查询失败
#             # -------------------------------------------------
#
#             if err != zed.ERROR_CODE.SUCCESS:
#
#                 return None
#
#             if point3D is None:
#
#                 return None
#
#             # =================================================
#             # 官方 XYZ
#             # 单位：米
#             # =================================================
#
#             X = float(point3D[0])
#             Y = float(point3D[1])
#             Z = float(point3D[2])
#
#             # =================================================
#             # 判断 NaN / Inf
#             # =================================================
#
#             if not np.isfinite(X):
#                 return None
#
#             if not np.isfinite(Y):
#                 return None
#
#             if not np.isfinite(Z):
#                 return None
#
#             # =================================================
#             # 距离
#             #
#             # 和官方示例完全一样
#             # =================================================
#
#             distance_m = math.sqrt(
#                 X * X +
#                 Y * Y +
#                 Z * Z
#             )
#
#             # =================================================
#             # 返回
#             #
#             # 保留毫米，方便你的 main.py 使用
#             # =================================================
#
#             return (
#                 X * 1000.0,
#                 Y * 1000.0,
#                 Z * 1000.0,
#                 distance_m,
#                 ix,
#                 iy
#             )
#
#         except Exception as e:
#
#             print(
#                 f"[ZED] 点云坐标获取失败: {e}"
#             )
#
#             return None
#
#
#     # =========================================================
#     # UDP 服务
#     # =========================================================
#
#     def socket_server_loop(self):
#
#         sock = socket.socket(
#             socket.AF_INET,
#             socket.SOCK_DGRAM
#         )
#
#         sock.bind(
#             (
#                 '127.0.0.1',
#                 self.port
#             )
#         )
#
#         sock.settimeout(0.5)
#
#         print(
#             f"[ZED] UDP 服务启动，"
#             f"端口 {self.port}"
#         )
#
#         while self.running:
#
#             try:
#
#                 data, addr = (
#                     sock.recvfrom(65535)
#                 )
#
#                 req = json.loads(
#                     data.decode()
#                 )
#
#                 cmd = req.get('cmd')
#
#                 # =================================================
#                 # 显示窗口
#                 # =================================================
#
#                 if cmd == 'show_window':
#
#                     self.window_visible = True
#
#                     self.display_text = ""
#
#                     self.display_text_expire = 0.0
#
#                     reply = {
#                         'status': 'ok'
#                     }
#
#                 # =================================================
#                 # 隐藏窗口
#                 # =================================================
#
#                 elif cmd == 'hide_window':
#
#                     self.window_visible = False
#
#                     reply = {
#                         'status': 'ok'
#                     }
#
#                 # =================================================
#                 # 获取坐标
#                 # =================================================
#
#                 elif cmd == 'get_coord':
#
#                     screen_x = req.get(
#                         'x',
#                         0
#                     )
#
#                     screen_y = req.get(
#                         'y',
#                         0
#                     )
#
#                     # -------------------------------------------------
#                     # 先判断鼠标是否在深度图区域
#                     # -------------------------------------------------
#
#                     ix, iy = (
#                         self.screen_to_image_coord(
#                             screen_x,
#                             screen_y
#                         )
#                     )
#
#                     if ix < 0 or iy < 0:
#
#                         # =================================================
#                         # 非常重要：
#                         #
#                         # 鼠标在深度图窗口外
#                         # 直接拒绝
#                         #
#                         # 不查询 point_cloud
#                         # =================================================
#
#                         reply = {
#                             'error': 'outside_depth_region'
#                         }
#
#                     else:
#
#                         res = (
#                             self.get_world_coord(
#                                 screen_x,
#                                 screen_y
#                             )
#                         )
#
#                         if res is None:
#
#                             reply = {
#                                 'error': 'invalid_depth'
#                             }
#
#                         else:
#
#                             X_mm = res[0]
#                             Y_mm = res[1]
#                             Z_mm = res[2]
#                             distance_m = res[3]
#
#                             zed_x = res[4]
#                             zed_y = res[5]
#
#                             reply = {
#
#                                 'x_mm': float(
#                                     X_mm
#                                 ),
#
#                                 'y_mm': float(
#                                     Y_mm
#                                 ),
#
#                                 'z_mm': float(
#                                     Z_mm
#                                 ),
#
#                                 'distance_m': float(
#                                     distance_m
#                                 ),
#
#                                 'image_x': int(
#                                     zed_x
#                                 ),
#
#                                 'image_y': int(
#                                     zed_y
#                                 )
#                             }
#
#                             # =================================================
#                             # 深度图显示信息
#                             # =================================================
#
#                             self.display_text = (
#
#                                 f"Screen:"
#                                 f"({float(screen_x):.0f},"
#                                 f"{float(screen_y):.0f})  "
#
#                                 f"ZED:"
#                                 f"({zed_x},"
#                                 f"{zed_y})  "
#
#                                 f"XYZ:"
#                                 f"({X_mm:.0f},"
#                                 f"{Y_mm:.0f},"
#                                 f"{Z_mm:.0f})mm  "
#
#                                 f"D:"
#                                 f"{distance_m:.2f}m"
#                             )
#
#                             self.display_text_expire = (
#                                 time.time() + 3.0
#                             )
#
#                 # =================================================
#                 # 退出
#                 # =================================================
#
#                 elif cmd == 'quit':
#
#                     self.running = False
#
#                     reply = {
#                         'status': 'ok'
#                     }
#
#                 # =================================================
#                 # 未知命令
#                 # =================================================
#
#                 else:
#
#                     reply = {
#                         'error': 'unknown_cmd'
#                     }
#
#                 # =================================================
#                 # 回复 main.py
#                 # =================================================
#
#                 sock.sendto(
#                     json.dumps(
#                         reply
#                     ).encode(),
#                     addr
#                 )
#
#             except socket.timeout:
#
#                 continue
#
#             except Exception as e:
#
#                 print(
#                     f"[ZED] UDP 错误: {e}"
#                 )
#
#         sock.close()
#
#
#     # =========================================================
#     # 深度图显示循环
#     # =========================================================
#
#     def display_loop(self):
#
#         # =====================================================
#         # 和官方示例一致
#         # =====================================================
#
#         runtime = zed.RuntimeParameters(
#             confidence_threshold=50
#         )
#
#         while self.running:
#
#             # =================================================
#             # 获取一帧
#             # =================================================
#
#             err = self.cam.grab(
#                 runtime
#             )
#
#             if err != zed.ERROR_CODE.SUCCESS:
#
#                 continue
#
#             # =================================================
#             # 官方深度图
#             # =================================================
#
#             self.cam.retrieve_image(
#                 self.depth_image,
#                 zed.VIEW.DEPTH
#             )
#
#             depth_img = (
#                 self.depth_image.get_data()
#             )
#
#             # =================================================
#             # 官方点云
#             #
#             # 和你的示例完全一致
#             # =================================================
#
#             self.cam.retrieve_measure(
#                 self.point_cloud,
#                 zed.MEASURE.XYZRGBA
#             )
#
#             # =================================================
#             # 显示窗口
#             # =================================================
#
#             if self.window_visible:
#
#                 if not self.window_created:
#
#                     # -------------------------------------------------
#                     # WINDOW_AUTOSIZE
#                     #
#                     # 保持和官方示例一致
#                     # -------------------------------------------------
#
#                     cv2.namedWindow(
#                         self.window_name,
#                         cv2.WINDOW_AUTOSIZE
#                     )
#
#                     # -------------------------------------------------
#                     # 窗口居中
#                     # -------------------------------------------------
#
#                     cv2.moveWindow(
#                         self.window_name,
#                         self.window_x,
#                         self.window_y
#                     )
#
#                     # -------------------------------------------------
#                     # 显示第一帧
#                     # -------------------------------------------------
#
#                     cv2.imshow(
#                         self.window_name,
#                         depth_img
#                     )
#
#                     cv2.waitKey(1)
#
#                     self.window_created = True
#
#                     print(
#                         "[ZED] 深度窗口已创建"
#                     )
#
#                     print(
#                         f"[ZED] Window: "
#                         f"({self.window_x}, "
#                         f"{self.window_y})"
#                         f" "
#                         f"{self.orig_w}x"
#                         f"{self.orig_h}"
#                     )
#
#                 # =================================================
#                 # 当前显示图像
#                 # =================================================
#
#                 display_img = depth_img.copy()
#
#                 # =================================================
#                 # 显示 XYZ 信息
#                 # =================================================
#
#                 if (
#                     self.display_text
#                     and
#                     self.display_text_expire
#                     > time.time()
#                 ):
#
#                     font = (
#                         cv2.FONT_HERSHEY_SIMPLEX
#                     )
#
#                     text = self.display_text
#
#                     (
#                         tw,
#                         th
#                     ), baseline = (
#                         cv2.getTextSize(
#                             text,
#                             font,
#                             0.7,
#                             2
#                         )
#                     )
#
#                     cv2.rectangle(
#                         display_img,
#                         (10, 10),
#                         (
#                             10 + tw + 10,
#                             10 + th + 10
#                         ),
#                         (0, 0, 0),
#                         -1
#                     )
#
#                     cv2.putText(
#                         display_img,
#                         text,
#                         (
#                             15,
#                             10 + th + 5
#                         ),
#                         font,
#                         0.7,
#                         (255, 255, 255),
#                         2,
#                         cv2.LINE_AA
#                     )
#
#                 # =================================================
#                 # 显示
#                 # =================================================
#
#                 cv2.imshow(
#                     self.window_name,
#                     display_img
#                 )
#
#                 key = (
#                     cv2.waitKey(1)
#                     & 0xFF
#                 )
#
#                 # =================================================
#                 # ESC
#                 # =================================================
#
#                 if key == 27:
#
#                     self.window_visible = False
#
#                     print(
#                         "🔘 ESC：退出 ZED 环境模式"
#                     )
#
#             else:
#
#                 # =================================================
#                 # 隐藏窗口
#                 # =================================================
#
#                 if self.window_created:
#
#                     cv2.destroyWindow(
#                         self.window_name
#                     )
#
#                     self.window_created = False
#
#                 time.sleep(0.005)
#
#         # =====================================================
#         # 程序退出
#         # =====================================================
#
#         cv2.destroyAllWindows()
#
#         self.window_created = False
#
#
#     # =========================================================
#     # 主运行
#     # =========================================================
#
#     def run(self):
#
#         if not self.init_camera():
#
#             return
#
#         # -----------------------------------------------------
#         # UDP线程
#         # -----------------------------------------------------
#
#         sock_thread = threading.Thread(
#             target=self.socket_server_loop,
#             daemon=True
#         )
#
#         sock_thread.start()
#
#         # -----------------------------------------------------
#         # ZED显示循环
#         # -----------------------------------------------------
#
#         self.display_loop()
#
#         # -----------------------------------------------------
#         # 关闭相机
#         # -----------------------------------------------------
#
#         if self.cam is not None:
#
#             self.cam.close()
#
#         print(
#             "[ZED] Camera closed"
#         )
#
#
# # =============================================================
# # MAIN
# # =============================================================
#
# if __name__ == '__main__':
#
#     server = ZEDServer()
#
#     server.run()

import sys
import json
import socket
import threading
import time
import math
import cv2
import numpy as np
import pyzed.sl as zed


class ZEDServer:

    def __init__(self, port=9999):

        self.port = port

        # =====================================================
        # ZED
        # =====================================================

        self.cam = None

        # 官方点云
        self.point_cloud = zed.Mat()

        # 官方深度图
        self.depth_image = zed.Mat()

        # =====================================================
        # ZED 原始图像尺寸
        # =====================================================

        self.orig_w = 1280
        self.orig_h = 720

        # =====================================================
        # 电脑屏幕尺寸
        # =====================================================

        self.screen_w = 1280
        self.screen_h = 720

        # =====================================================
        # 深度窗口
        # =====================================================

        self.window_name = "ZED Environment"

        self.window_visible = False
        self.window_created = False

        # =====================================================
        # 运行状态
        # =====================================================

        self.running = True

        # =====================================================
        # 坐标显示
        # =====================================================

        self.display_text = ""
        self.display_text_expire = 0.0


    # =========================================================
    # 初始化 ZED
    # =========================================================

    def init_camera(self):

        cam = zed.Camera()

        init = zed.InitParameters()

        # -----------------------------------------------------
        # 完全按照官方示例
        # -----------------------------------------------------

        init.camera_resolution = zed.RESOLUTION.HD720

        init.depth_mode = zed.DEPTH_MODE.NEURAL_PLUS

        init.coordinate_units = zed.UNIT.METER

        init.depth_maximum_distance = 40

        init.camera_fps = 30

        # -----------------------------------------------------
        # 打开相机
        # -----------------------------------------------------

        err = cam.open(init)

        if err != zed.ERROR_CODE.SUCCESS:

            print(
                f"ZED 相机打开失败: {err}"
            )

            return False

        self.cam = cam

        # -----------------------------------------------------
        # 获取真实分辨率
        # -----------------------------------------------------

        camera_info = cam.get_camera_information()

        self.orig_w = (
            camera_info
            .camera_configuration
            .resolution
            .width
        )

        self.orig_h = (
            camera_info
            .camera_configuration
            .resolution
            .height
        )

        print(
            "Camera initialized successfully"
        )

        print(
            f"ZED resolution: "
            f"{self.orig_w} x {self.orig_h}"
        )

        # -----------------------------------------------------
        # 获取电脑屏幕尺寸
        # -----------------------------------------------------

        try:

            import pyautogui

            self.screen_w, self.screen_h = (
                pyautogui.size()
            )

        except Exception as e:

            print(
                f"获取屏幕尺寸失败: {e}"
            )

            return False

        print(
            f"Screen size: "
            f"{self.screen_w} x "
            f"{self.screen_h}"
        )

        print(
            f"ZED image size: "
            f"{self.orig_w} x "
            f"{self.orig_h}"
        )

        print(
            "ZED 深度图将以全屏方式显示"
        )

        return True


    # =========================================================
    # 全屏显示坐标
    #
    # 屏幕：
    #
    #     screen_w × screen_h
    #
    # ZED原始图像：
    #
    #     orig_w × orig_h
    #
    # 全屏显示时：
    #
    #     全屏坐标
    #          ↓
    #     ZED原始坐标
    # =========================================================

    def screen_to_image_coord(
        self,
        screen_x,
        screen_y
    ):

        try:

            screen_x = float(screen_x)
            screen_y = float(screen_y)

        except Exception:

            return -1, -1

        # =====================================================
        # 1. 判断是否在屏幕范围内
        # =====================================================

        if screen_x < 0:
            return -1, -1

        if screen_y < 0:
            return -1, -1

        if screen_x >= self.screen_w:
            return -1, -1

        if screen_y >= self.screen_h:
            return -1, -1

        # =====================================================
        # 2. 全屏坐标 -> ZED原始图像坐标
        # =====================================================

        ix = int(
            screen_x *
            self.orig_w /
            self.screen_w
        )

        iy = int(
            screen_y *
            self.orig_h /
            self.screen_h
        )

        # =====================================================
        # 3. 最终边界保护
        # =====================================================

        if ix < 0:
            return -1, -1

        if iy < 0:
            return -1, -1

        if ix >= self.orig_w:
            ix = self.orig_w - 1

        if iy >= self.orig_h:
            iy = self.orig_h - 1

        return ix, iy


    # =========================================================
    # 官方 ZED 点云查询
    #
    # 核心：
    #
    #     point_cloud.get_value(x, y)
    #
    # 和官方示例完全一致
    # =========================================================

    def get_world_coord(
        self,
        screen_x,
        screen_y
    ):

        if self.cam is None:
            return None

        if not self.window_visible:
            return None

        # =====================================================
        # 全屏坐标 -> ZED原始图像坐标
        # =====================================================

        ix, iy = self.screen_to_image_coord(
            screen_x,
            screen_y
        )

        # -----------------------------------------------------
        # 屏幕外
        # -----------------------------------------------------

        if ix < 0 or iy < 0:

            return None

        try:

            # =================================================
            # 官方示例核心
            # =================================================

            err, point3D = (
                self.point_cloud.get_value(
                    ix,
                    iy
                )
            )

            # -------------------------------------------------
            # ZED查询失败
            # -------------------------------------------------

            if err != zed.ERROR_CODE.SUCCESS:

                return None

            if point3D is None:

                return None

            # =================================================
            # 官方 XYZ
            #
            # 当前单位：
            #     UNIT.METER
            #
            # 所以这里得到的是米
            # =================================================

            X = float(point3D[0])
            Y = float(point3D[1])
            Z = float(point3D[2])

            # =================================================
            # 判断 NaN / Inf
            # =================================================

            if not np.isfinite(X):
                return None

            if not np.isfinite(Y):
                return None

            if not np.isfinite(Z):
                return None

            # =================================================
            # 距离
            #
            # 和官方示例完全一样
            # =================================================

            distance_m = math.sqrt(
                X * X +
                Y * Y +
                Z * Z
            )

            # =================================================
            # 返回
            #
            # XYZ转换成毫米
            # =================================================

            return (
                X * 1000.0,
                Y * 1000.0,
                Z * 1000.0,
                distance_m,
                ix,
                iy
            )

        except Exception as e:

            print(
                f"[ZED] 点云坐标获取失败: {e}"
            )

            return None


    # =========================================================
    # UDP 服务
    # =========================================================

    def socket_server_loop(self):

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        sock.bind(
            (
                '127.0.0.1',
                self.port
            )
        )

        sock.settimeout(0.5)

        print(
            f"[ZED] UDP 服务启动，"
            f"端口 {self.port}"
        )

        while self.running:

            try:

                data, addr = (
                    sock.recvfrom(65535)
                )

                req = json.loads(
                    data.decode()
                )

                cmd = req.get('cmd')

                # =================================================
                # 显示窗口
                # =================================================

                if cmd == 'show_window':

                    self.window_visible = True

                    self.display_text = ""

                    self.display_text_expire = 0.0

                    reply = {
                        'status': 'ok'
                    }

                # =================================================
                # 隐藏窗口
                # =================================================

                elif cmd == 'hide_window':

                    self.window_visible = False

                    reply = {
                        'status': 'ok'
                    }

                # =================================================
                # 获取坐标
                # =================================================

                elif cmd == 'get_coord':

                    screen_x = req.get(
                        'x',
                        0
                    )

                    screen_y = req.get(
                        'y',
                        0
                    )

                    # -------------------------------------------------
                    # 全屏坐标 -> ZED坐标
                    # -------------------------------------------------

                    ix, iy = (
                        self.screen_to_image_coord(
                            screen_x,
                            screen_y
                        )
                    )

                    if ix < 0 or iy < 0:

                        # =================================================
                        # 屏幕范围外
                        # =================================================

                        reply = {
                            'error': 'outside_depth_region'
                        }

                    else:

                        res = (
                            self.get_world_coord(
                                screen_x,
                                screen_y
                            )
                        )

                        if res is None:

                            reply = {
                                'error': 'invalid_depth'
                            }

                        else:

                            X_mm = res[0]
                            Y_mm = res[1]
                            Z_mm = res[2]

                            distance_m = res[3]

                            zed_x = res[4]
                            zed_y = res[5]

                            reply = {

                                'x_mm': float(
                                    X_mm
                                ),

                                'y_mm': float(
                                    Y_mm
                                ),

                                'z_mm': float(
                                    Z_mm
                                ),

                                'distance_m': float(
                                    distance_m
                                ),

                                'image_x': int(
                                    zed_x
                                ),

                                'image_y': int(
                                    zed_y
                                )
                            }

                            # =================================================
                            # 深度图显示信息
                            # =================================================

                            self.display_text = (

                                f"Screen:"
                                f"({float(screen_x):.0f},"
                                f"{float(screen_y):.0f})  "

                                f"ZED:"
                                f"({zed_x},"
                                f"{zed_y})  "

                                f"XYZ:"
                                f"({X_mm:.0f},"
                                f"{Y_mm:.0f},"
                                f"{Z_mm:.0f})mm  "

                                f"D:"
                                f"{distance_m:.2f}m"
                            )

                            self.display_text_expire = (
                                time.time() + 3.0
                            )

                # =================================================
                # 退出
                # =================================================

                elif cmd == 'quit':

                    self.running = False

                    reply = {
                        'status': 'ok'
                    }

                # =================================================
                # 未知命令
                # =================================================

                else:

                    reply = {
                        'error': 'unknown_cmd'
                    }

                # =================================================
                # 回复 main.py
                # =================================================

                sock.sendto(
                    json.dumps(
                        reply
                    ).encode(),
                    addr
                )

            except socket.timeout:

                continue

            except Exception as e:

                print(
                    f"[ZED] UDP 错误: {e}"
                )

        sock.close()


    # =========================================================
    # 深度图显示循环
    # =========================================================

    def update_coordinate_measure(self, rgb_error=None):
        return self.cam.retrieve_measure(self.point_cloud, zed.MEASURE.XYZRGBA)

    def display_loop(self):

        # =====================================================
        # 和官方示例一致
        # =====================================================

        runtime = zed.RuntimeParameters(
            confidence_threshold=50
        )

        while self.running:

            # =================================================
            # 获取一帧
            # =================================================

            err = self.cam.grab(
                runtime
            )

            if err != zed.ERROR_CODE.SUCCESS:

                continue

            # 实物环境（左目 RGB）
            rgb_error = self.cam.retrieve_image(
                self.depth_image,
                zed.VIEW.LEFT
            )

            rgb_img = self.depth_image.get_data()

            # 官方点云（保持不变）
            self.update_coordinate_measure(rgb_error)

            # =================================================
            # 显示窗口
            # =================================================

            if self.window_visible:

                if not self.window_created:

                    # -------------------------------------------------
                    # 普通窗口
                    # -------------------------------------------------

                    cv2.namedWindow(
                        self.window_name,
                        cv2.WINDOW_NORMAL
                    )

                    # -------------------------------------------------
                    # 设置全屏
                    # -------------------------------------------------

                    cv2.setWindowProperty(
                        self.window_name,
                        cv2.WND_PROP_FULLSCREEN,
                        cv2.WINDOW_FULLSCREEN
                    )

                    # -------------------------------------------------
                    # 显示第一帧
                    #
                    # OpenCV会将1280×720
                    # 自动铺满全屏
                    # -------------------------------------------------

                    display_img = cv2.resize(
                        rgb_img,
                        (self.screen_w, self.screen_h),
                        interpolation=cv2.INTER_LINEAR
                    )

                    cv2.imshow(
                        self.window_name,
                        display_img
                    )

                    cv2.waitKey(1)

                    self.window_created = True

                    print(
                        "[ZED] 深度窗口已全屏创建"
                    )

                    print(
                        f"[ZED] ZED image: "
                        f"{self.orig_w}x"
                        f"{self.orig_h}"
                    )

                    print(
                        f"[ZED] Fullscreen: "
                        f"{self.screen_w}x"
                        f"{self.screen_h}"
                    )

                # =================================================
                # 当前显示图像
                # =================================================

                display_img = cv2.resize(
                    rgb_img,
                    (self.screen_w, self.screen_h),
                    interpolation=cv2.INTER_LINEAR
                )

                # =================================================
                # 显示 XYZ 信息
                # =================================================

                if (
                    self.display_text
                    and
                    self.display_text_expire
                    > time.time()
                ):

                    font = (
                        cv2.FONT_HERSHEY_SIMPLEX
                    )

                    text = self.display_text

                    (
                        tw,
                        th
                    ), baseline = (
                        cv2.getTextSize(
                            text,
                            font,
                            0.7,
                            2
                        )
                    )

                    cv2.rectangle(
                        display_img,
                        (10, 10),
                        (
                            10 + tw + 10,
                            10 + th + 10
                        ),
                        (0, 0, 0),
                        -1
                    )

                    cv2.putText(
                        display_img,
                        text,
                        (
                            15,
                            10 + th + 5
                        ),
                        font,
                        0.7,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA
                    )

                # =================================================
                # 显示
                # =================================================

                cv2.imshow(
                    self.window_name,
                    display_img
                )

                key = (
                    cv2.waitKey(1)
                    & 0xFF
                )

                # =================================================
                # ESC
                # =================================================

                if key == 27:

                    self.window_visible = False

                    print(
                        "🔘 ESC：退出 ZED 环境模式"
                    )

            else:

                # =================================================
                # 隐藏窗口
                # =================================================

                if self.window_created:

                    cv2.destroyWindow(
                        self.window_name
                    )

                    self.window_created = False

                time.sleep(0.005)

        # =====================================================
        # 程序退出
        # =====================================================

        cv2.destroyAllWindows()

        self.window_created = False


    # =========================================================
    # 主运行
    # =========================================================

    def run(self):

        if not self.init_camera():

            return

        # -----------------------------------------------------
        # UDP线程
        # -----------------------------------------------------

        sock_thread = threading.Thread(
            target=self.socket_server_loop,
            daemon=True
        )

        sock_thread.start()

        # -----------------------------------------------------
        # ZED显示循环
        # -----------------------------------------------------

        self.display_loop()

        # -----------------------------------------------------
        # 关闭相机
        # -----------------------------------------------------

        if self.cam is not None:

            self.cam.close()

        print(
            "[ZED] Camera closed"
        )


# =============================================================
# MAIN
# =============================================================

if __name__ == '__main__':

    server = ZEDServer()

    server.run()
