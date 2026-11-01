import math
import numpy as np
import cv2
import pyzed.sl as zed
import time


cam = zed.Camera()
init = zed.InitParameters()
init.camera_resolution = zed.RESOLUTION.HD720
init.depth_mode = zed.DEPTH_MODE.ULTRA
init.coordinate_units = zed.UNIT.METER
# init.depth_minimum_distance = 1
init.depth_maximum_distance = 40

err = cam.open(init)

if err != zed.ERROR_CODE.SUCCESS:
    print(repr(err))
    cam.close()
    exit(1)

# 参数
image = zed.Mat()
runtime = zed.RuntimeParameters(confidence_threshold=50)
image_left = zed.Mat()
image_right = zed.Mat()
confidence_map = zed.Mat()
depth_image = zed.Mat(image.get_width(), image.get_height(), zed.MAT_TYPE.U8_C4)
depth_map = zed.Mat()
point_cloud = zed.Mat()


# --------------------------鼠标回调函数---------------------------------------------------------
#   event               鼠标事件
#   param               输入参数
# -----------------------------------------------------------------------------------------------

def onmouse_pick_points_1(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        # threeD = param
        print('\n像素坐标 x = %d, y = %d' % (x, y))
        # print("世界坐标是：", threeD[y][x][0], threeD[y][x][1], threeD[y][x][2], "mm")
        err, point3D = point_cloud.get_value(x, y)
        x = point3D[0]
        y = point3D[1]
        z = point3D[2]
        color = point3D[3]
        print("世界坐标xyz 是:%.3f,%.3f,%.3f "% (x,y,z))

        distance = math.sqrt(point3D[0] * point3D[0] + point3D[1] * point3D[1] + point3D[2] * point3D[2])
        print("距离是：", distance, "m")


while True:
    cam.grab(runtime)
    # 左
    cam.retrieve_image(image_left, zed.VIEW.LEFT)
    image_cv_left = image_left.get_data()
    # 右
    cam.retrieve_image(image_right, zed.VIEW.RIGHT)
    image_cv_right = image_right.get_data()
    # 深度
    cam.retrieve_image(depth_image, zed.VIEW.DEPTH)
    cam.retrieve_measure(confidence_map, zed.MEASURE.CONFIDENCE)
    # image_cv_left = cv2.cvtColor(image_cv_left, 1)
    # image_cv_right = cv2.cvtColor(image_cv_right, 1)
    image_depth = depth_image.get_data()
    # 点云
    cam.retrieve_measure(point_cloud, zed.MEASURE.XYZRGBA)

    # # 获得中心点
    # x = image.get_width() / 2
    # y = image.get_height() / 2
    # err, point_cloud_value = point_cloud.get_value(x, y)
    #
    # distance = math.sqrt(
    #     point_cloud_value[0] * point_cloud_value[0] +
    #     point_cloud_value[1] * point_cloud_value[1] +
    #     point_cloud_value[2] * point_cloud_value[2]
    # )

    # 鼠标回调事件
    cv2.namedWindow("depth", cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback("depth", onmouse_pick_points_1)

    # cv2.imshow('left', image_cv_left)
    # cv2.imshow('right', image_cv_right)
    cv2.imshow('depth', image_depth)
    if cv2.waitKey(1) & 0xFF == 27:
        break

