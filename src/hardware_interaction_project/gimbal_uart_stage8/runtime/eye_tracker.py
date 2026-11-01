import cv2
import mediapipe as mp
import numpy as np
from typing import Tuple, Optional



class EyeTracker:
    def __init__(self):
        # 初始化MediaPipe人脸网格
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,  # 只检测一个人脸（max_num_faces=1）
            refine_landmarks=True,
            min_detection_confidence=0.7,  # 置信度阈值设为 0.7
            min_tracking_confidence=0.7
        )

        # 虹膜关键点索引
        self.LEFT_IRIS = [474, 475, 476, 477]  # 虹膜关键点索引
        self.RIGHT_IRIS = [469, 470, 471, 472]
        self.LEFT_EYE_POINTS = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
        self.RIGHT_EYE_POINTS = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
        # 眼睛轮廓点关键点索引qqqq

        # 校准参数
        self.calibration_points = []  # 眼睛位置
        self.screen_points = []  # 对应的屏幕目标点
        self.is_calibrated = False
        self.calibration_matrix = None  # 从眼睛坐标到屏幕坐标的映射矩阵

        # 增强的平滑滤波
        self.positions_history = []
        self.max_history = 8
        self.stability_threshold = 5.0
        # 使用历史位置做加权平均
        # 检测失效时停止输出，不能把旧坐标当作新观测。
        # 精度优化参数
        self.quality_threshold = 0.8
        self.last_good_position = None
        self.tracking_ready = False
        self.tracking_recovery = True
        self.tracking_generation = 0
        self.tracking_loss_reason = "initializing"
        self.recovery_candidates = []
        self.recovery_frames = 5
        self.last_raw_position = None
        self.detection_quality = None
        self.frame_size = None  # 实际送入检测的图像尺寸 (宽, 高)
        self._face_reset_for_loss = False

        # 保留这个用于重新校准提示
        self.recalibration_prompt = False

        self.current_blink_status = None  # 新增：存储当前帧的眨眼状态
        self.blink_rearm_required = True
        self.right_wink_started_at = None
        self.right_wink_last_at = None
        self.right_wink_frames = 0
        self.right_wink_seconds = 0.0
        self.right_wink_confirmed = False
        self.right_wink_release_started_at = None
        self.right_wink_candidate_active = False
        self.right_wink_min_seconds = 0.12
        self.right_wink_min_frames = 2
        self.right_wink_close_threshold = 0.50
        self.right_wink_other_eye_max_drop = 0.45
        self.right_wink_asymmetry_threshold = 0.25
        self.right_wink_open_threshold = 0.30
        self.right_wink_max_gap_seconds = 0.25
        self.right_wink_release_max_seconds = 0.35
        self.last_blink_observation = None
        self.stable_pos_buffer = []  # 新增：用于坐标回溯的缓冲区
        self.lock_frames_remaining = 0  # 新增：锁定计数器
        self.last_locked_output = (0, 0)  # 新增：锁定时的坐标快照

        self.blink_lock_active = False  # 眨眼锁定激活标志
        self.pre_blink_positions = []  # 眨眼前的历史位置缓存

        self.mouth_scroll_direction = -1  # 默认向下滚动
        self.mouth_open_counter = 0
        self.last_scroll_time = 0
        self.mouth_control_enabled = False  # 校准后启用
        self.mouth_is_open = False
        self.mouth_open_ratio = None
        self.mouth_open_threshold = 0.4
        self.mouth_close_threshold = 0.25

        # 新增：动作互斥相关属性（修复错误）
        self.current_action = None  # 当前正在执行的动作: "blink" 或 "mouth"
        self.last_action_time = 0
        self.action_cooldown = 0.3  # 动作冷却时间（秒）

        # 嘴唇关键点
        self.MOUTH_LEFT = [61, 146, 91]  # 左嘴角附近点
        self.MOUTH_RIGHT = [291, 375, 321]  # 右嘴角附近点
        self.MOUTH_CENTER = [13, 14, 78, 308]  # 嘴巴中心点


    def _invalidate_tracking(self, reason):
        if self.tracking_ready:
            self.tracking_generation += 1
        self.tracking_ready = False
        self.tracking_recovery = True
        self.tracking_loss_reason = reason
        self.recovery_candidates.clear()
        self.last_good_position = None
        self.positions_history.clear()
        self.stable_pos_buffer.clear()
        self.pre_blink_positions.clear()
        self.lock_frames_remaining = 0
        self.blink_lock_active = False
        self.current_blink_status = None
        self.blink_active = False
        self.blink_rearm_required = True
        self._clear_right_wink_candidate()
        self.last_blink_observation = None
        # 只清 MediaPipe 的短期跟踪，一次丢脸只重置一次；校准保持不变。
        if reason == "no_face" and not self._face_reset_for_loss:
            self._face_reset_for_loss = True
            self.face_mesh.reset()

    def _accept_recovery_candidate(self, point):
        if self.tracking_ready:
            return True
        # 按摄像头的连续有效帧确认重获。移动、慢帧和旧校准范围
        # 不代表眼睛没有被检测到；无效帧会在 _invalidate_tracking 清计数。
        self.recovery_candidates.append(point)
        self.tracking_loss_reason = "recovering"
        if len(self.recovery_candidates) < self.recovery_frames:
            return False
        self.recovery_candidates.clear()
        self.tracking_ready = True
        self.tracking_recovery = False
        self.tracking_loss_reason = None
        self.recalibration_prompt = False
        self._face_reset_for_loss = False
        return True

    def detect_iris_position(self, frame: np.ndarray) -> Optional[Tuple[float, float]]:
        ''' -> Optional[Tuple[float, float]]: 是 Python 中的类型提示（Type Hint）
            ->：表示后面跟着的是返回值类型
            Optional[]：表示返回值可能为空（可以是None）
            Tuple[float, float]：表示一个包含两个浮点数的元组'''

        """检测虹膜位置（增强版）"""
        try:
            self.last_raw_position = None
            self.detection_quality = None
            h, w = frame.shape[:2]
            if h <= 0 or w <= 0:
                raise ValueError("Invalid camera frame size")
            self.frame_size = (w, h)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)
            #将图像传入 MediaPipe 的人脸网格模型，返回检测结果，包含 468 个面部关键点

            if not results.multi_face_landmarks:
                self._invalidate_tracking("no_face")
                return None

            face_landmarks = results.multi_face_landmarks[0]
            #multi_face_landmarks 是一个列表，包含检测到的每个人脸的关键点，取第一个

            # 获取左右眼虹膜中心
            left_iris = self._get_iris_center(face_landmarks, self.LEFT_IRIS, w, h) #虹膜关键点索引
            right_iris = self._get_iris_center(face_landmarks, self.RIGHT_IRIS, w, h)

            if left_iris and right_iris:
                # 计算双眼中心平均位置
                avg_x = (left_iris[0] + right_iris[0]) / 2
                avg_y = (left_iris[1] + right_iris[1]) / 2
                point = (avg_x, avg_y)
                self.last_raw_position = point
                if not all(np.isfinite(x) and 0 <= x < w and np.isfinite(y) and 0 <= y < h
                           for x, y in (left_iris, right_iris)):
                    self._invalidate_tracking("invalid_geometry")
                    return None

                # 计算检测质量
                quality = self._calculate_detection_quality(face_landmarks, w, h)
                self.detection_quality = quality

                # 高度对称性仅用于诊断，歪头等情况不能单凭低分否决检测。
                # 非法关键点仍会使当前帧失效，不能用旧坐标继续控制。
                if not np.isfinite(quality):
                    self._invalidate_tracking("invalid_geometry")
                    return None
                if not self._accept_recovery_candidate(point):
                    return None

                # 应用增强平滑
                smoothed_pos = self._enhanced_smoothing(point)
                self.last_good_position = smoothed_pos
                return smoothed_pos

            self._invalidate_tracking("invalid_geometry")
            return None
        except Exception as e:
            print(f"虹膜检测错误: {e}")
            self._invalidate_tracking("detection_error")
            return None
        #使用 try-except 捕获所有可能的错误，发生任何异常时打印错误并返回 None



    def _get_iris_center(self, landmarks, iris_indices, w, h):
        """获取虹膜中心坐标"""
        iris_points = []
        for index in iris_indices:
            lm = landmarks.landmark[index]  # 获取关键点
            x, y = int(lm.x * w), int(lm.y * h)   # 转换为像素坐标
            iris_points.append((x, y))

        if iris_points:
            # 计算所有虹膜点的平均位置
            center_x = sum([p[0] for p in iris_points]) / len(iris_points)
            center_y = sum([p[1] for p in iris_points]) / len(iris_points)
            return (center_x, center_y)
        return None

    def _calculate_detection_quality(self, landmarks, w, h):
        """计算检测质量"""
        try:
            # 检查眼睛关键点的对称性
            left_eye_pts = []
            right_eye_pts = []

            for idx in self.LEFT_EYE_POINTS:
                lm = landmarks.landmark[idx]
                left_eye_pts.append((lm.x * w, lm.y * h))

            for idx in self.RIGHT_EYE_POINTS:
                lm = landmarks.landmark[idx]
                right_eye_pts.append((lm.x * w, lm.y * h))

            if not np.all(np.isfinite(left_eye_pts + right_eye_pts)):
                return float("nan")

            # 计算左右眼的中心距离（应该大致对称）
            left_center = np.mean(left_eye_pts, axis=0)
            right_center = np.mean(right_eye_pts, axis=0)

            # 简单的质量评估：眼睛应该在相似的高度
            height_diff = abs(left_center[1] - right_center[1])
            max_allowed_diff = h * 0.1  # 允许10%的高度差异

            quality = max(0, 1 - height_diff / max_allowed_diff)
            return quality

        except Exception:
            return float("nan")  # 无法计算的关键点不能伪装成正常诊断分数


    def _enhanced_smoothing(self, new_position: Tuple[float, float]) -> Tuple[float, float]:
        """
        自适应自适应平滑滤波：
        1. 计算移动速度（瞬时位移）
        2. 速度越快，平滑越小（保证灵敏）
        3. 速度越慢，平滑越大（保证稳定）
        4. 引入微动死区，消除震颤
        """
        if self.last_good_position is None:
            self.last_good_position = new_position
            return new_position

        # 1. 计算当前点与上一点的物理距离（位移速度）
        dx = new_position[0] - self.last_good_position[0]
        dy = new_position[1] - self.last_good_position[1]
        distance = np.sqrt(dx ** 2 + dy ** 2)

        # 2. 设置死区 (Deadzone)
        # 如果位移小于 1.5 像素，认为是个人的微小震颤，直接忽略
        if distance < 1:
            return self.last_good_position

        # 3. 计算自适应平滑因子 (Dynamic Alpha)
        # distance 越大（移动快），alpha 越小（倾向于使用新值）
        # distance 越小（移动慢），alpha 越大（倾向于使用旧值，极其稳定）
        # 使用 sigmoid 或线性映射将距离映射到 [0.6, 0.98] 之间
        # alpha = 0.85 - (min(distance, 30) / 30) * 0.45
        #
        # # 强制限制 alpha 范围，防止因移动过快导致跳变
        # alpha = max(0.4, min(0.85, alpha))
        # 原：0.85 - (min(distance,30)/30)*0.45 → 范围 0.4-0.85
        # 新：0.82 - (min(distance,25)/25)*0.37 → 范围 0.45-0.82
        #alpha = 0.82 - (min(distance, 25) / 25) * 0.37
        alpha = 0.75 - (min(distance, 20) / 20) * 0.40  # 稍微压低基准值

        # 范围限制：从[0.4,0.85]调整为[0.45,0.82]
        #alpha = max(0.45, min(0.82, alpha))
        alpha = max(0.35, min(0.75, alpha)) # 下限放开到 0.35，让快速移动更暴力

        # 4. 执行平滑计算
        smoothed_x = alpha * self.last_good_position[0] + (1 - alpha) * new_position[0]
        smoothed_y = alpha * self.last_good_position[1] + (1 - alpha) * new_position[1]

        # 5. 更新历史记录用于二级滤波
        self.positions_history.append((smoothed_x, smoothed_y))
        if len(self.positions_history) > 10:
            self.positions_history.pop(0)

        # 返回最终结果
        result = (smoothed_x, smoothed_y)
        self.last_good_position = result
        return result

    def add_calibration_point(self, eye_pos: Tuple[float, float], screen_pos: Tuple[float, float]):
        """添加校准点"""
        self.calibration_points.append(eye_pos)
        self.screen_points.append(screen_pos)
        print(f"添加校准点: 眼睛({eye_pos[0]:.1f}, {eye_pos[1]:.1f}) -> 屏幕({screen_pos[0]}, {screen_pos[1]})")

    def perform_calibration(self):
        """带标准化的稳健多项式校准"""
        if len(self.calibration_points) < 6:
            return False

        try:
            eye_points = np.array(self.calibration_points)
            screen_points = np.array(self.screen_points)

            # 1. 记录 Min-Max 参数（用于预测时的标准化）
            self.eye_min = np.min(eye_points, axis=0)
            self.eye_max = np.max(eye_points, axis=0)
            eye_range = np.where((self.eye_max - self.eye_min) == 0, 1, self.eye_max - self.eye_min)
            self.eye_range = eye_range

            # 2. 标准化眼睛坐标到 [0, 1]
            eye_norm = (eye_points - self.eye_min) / self.eye_range

            # 3. 构造特征：使用 [x, y, x*y, 1]
            # 注意：这里去掉了 x^2，因为 x*y 对角线修正足够了，且不会像二次方那样导致边缘起飞
            def get_stable_features(pts):
                x = pts[:, 0]
                y = pts[:, 1]
                return np.stack([x, y, x * y, np.ones_like(x)], axis=1)

            A = get_stable_features(eye_norm)
            B = screen_points

            # 4. 使用岭回归 (Ridge) 求解，增加 lambda=0.01 防止外推过猛
            lambda_reg = 0.03
            ATA = A.T @ A
            I = np.eye(ATA.shape[0])
            self.poly_matrix = np.linalg.solve(ATA + lambda_reg * I, A.T @ B)

            self.is_calibrated = True
            return True
        except Exception as e:
            print(f"校准出错: {e}")
            return False

    # def _fallback_calibration(self):
    #     """回退校准方法"""
    #     print("使用回退校准方法...")
    #     try:
    #         eye_points = np.array(self.calibration_points)
    #         screen_points = np.array(self.screen_points)
    #
    #         # 简单的线性映射
    #         eye_min = np.min(eye_points, axis=0)
    #         eye_max = np.max(eye_points, axis=0)
    #         screen_min = np.min(screen_points, axis=0)
    #         screen_max = np.max(screen_points, axis=0)
    #
    #         scale_x = (screen_max[0] - screen_min[0]) / (eye_max[0] - eye_min[0])
    #         scale_y = (screen_max[1] - screen_min[1]) / (eye_max[1] - eye_min[1])
    #
    #         offset_x = screen_min[0] - eye_min[0] * scale_x
    #         offset_y = screen_min[1] - eye_min[1] * scale_y
    #
    #         self.calibration_matrix = np.array([
    #             [scale_x, 0, offset_x],
    #             [0, scale_y, offset_y]
    #         ])
    #
    #         # if self.use_adaptive:
    #         #     self.adaptive_calibrator.set_initial_calibration(self.calibration_matrix)
    #
    #         self.is_calibrated = True
    #         print("回退校准完成")
    #         return True
    #
    #     except Exception as e:
    #         print(f"回退校准失败: {e}")
    #         return False

    def _calculate_calibration_error(self):
        """计算校准误差"""
        if not self.is_calibrated or self.calibration_matrix is None:
            return float('inf')

        errors = []
        screen_size = self._get_current_screen_size()


        for i, (eye_pos, actual_screen_pos) in enumerate(zip(self.calibration_points, self.screen_points)):
            try:
                predicted = self.to_screen_coordinates(eye_pos, screen_size)
                error = np.sqrt((predicted[0] - actual_screen_pos[0]) ** 2 +
                                (predicted[1] - actual_screen_pos[1]) ** 2)
                errors.append(error)
                # print(
                #    f"点{i + 1}: 预测({predicted[0]:.1f}, {predicted[1]:.1f}) vs 实际({actual_screen_pos[0]}, {actual_screen_pos[1]}) -> 误差: {error:.1f}像素")
            except Exception as e:
                print(f"计算点{i + 1}误差时出错: {e}")
                # avg_error = np.mean(errors) if errors else float('inf')
                # print(f"DEBUG: 平均误差 = {avg_error}")  # 添加调试
                errors.append(1000)  # 大误差值

        avg_error = np.mean(errors) if errors else float('inf')
        #print(f"各点误差: {[f'{e:.1f}' for e in errors]}")
        return avg_error

    # def _get_current_screen_size(self):
    #     """获取当前屏幕尺寸"""
    #     try:
    #         import pyautogui
    #         return pyautogui.size()
    #     except:
    #         return (2880, 1800)  # 默认值

    def _map_normalized(self, feat):
        return feat @ self.poly_matrix

    def to_screen_coordinates(self, eye_pos: Tuple[float, float], screen_size: Tuple[int, int]) -> Tuple[float, float]:
        """将眼睛坐标转换为屏幕坐标（增强眨眼锁定版）"""

        # 1. 确保缓冲区和锁定状态已初始化
        if not hasattr(self, "stable_pos_buffer"):
            self.stable_pos_buffer = []
            self.lock_frames_remaining = 0
            self.last_locked_output = (screen_size[0] // 2, screen_size[1] // 2)
            self.pre_blink_positions = []  # 新增：眨眼前的历史位置缓存
            self.blink_lock_active = False  # 新增：眨眼锁定激活标志

        # 获取默认值
        final_x, final_y = self.last_locked_output

        # 2. 检查眨眼锁定状态
        is_blinking = getattr(self, "current_blink_status", None) in ["double", "right"]

        # 新增：眨眼检测时的处理
        if is_blinking:
            # 如果刚刚开始眨眼，记录眨眼前的位置
            if not self.blink_lock_active:
                self.blink_lock_active = True

                # 从历史缓冲区获取眨眼前的位置（取4-6帧前的平均，避免眨眼瞬间的变形）
                if len(self.stable_pos_buffer) >= 6:
                    # 取倒数第4到第6帧（避开眨眼瞬间）
                    pre_frames = self.stable_pos_buffer[-6:-2]
                    if pre_frames:
                        avg_x = sum(p[0] for p in pre_frames) / len(pre_frames)
                        avg_y = sum(p[1] for p in pre_frames) / len(pre_frames)
                        self.last_locked_output = (avg_x, avg_y)
                elif len(self.stable_pos_buffer) >= 3:
                    # 如果历史帧不够，取最早几帧的平均
                    avg_x = sum(p[0] for p in self.stable_pos_buffer) / len(self.stable_pos_buffer)
                    avg_y = sum(p[1] for p in self.stable_pos_buffer) / len(self.stable_pos_buffer)
                    self.last_locked_output = (avg_x, avg_y)

            # 延长锁定帧数，确保整个眨眼过程都锁定
            self.lock_frames_remaining = 8  # 增加锁定帧数到10帧
            return self.last_locked_output

        # 如果锁定帧数还在，继续返回锁定位置
        if self.lock_frames_remaining > 0:
            self.lock_frames_remaining -= 1
            # 如果锁定即将结束（最后2帧），开始轻微向新位置过渡
            if self.lock_frames_remaining <= 2 and len(self.stable_pos_buffer) > 0:
                # 获取最新的实际位置
                if eye_pos and self.is_calibrated:
                    try:
                        # 计算新位置
                        ex_norm = (eye_pos[0] - self.eye_min[0]) / self.eye_range[0]
                        ey_norm = (eye_pos[1] - self.eye_min[1]) / self.eye_range[1]
                        feat = np.array([ex_norm, ey_norm, ex_norm * ey_norm, 1])
                        res = self._map_normalized(feat)
                        new_x, new_y = res[0], res[1]
                        new_x = max(0, min(new_x, screen_size[0]))
                        new_y = max(0, min(new_y, screen_size[1]))

                        # 与锁定位置进行混合过渡
                        alpha = 0.3  # 过渡系数
                        final_x = self.last_locked_output[0] * (1 - alpha) + new_x * alpha
                        final_y = self.last_locked_output[1] * (1 - alpha) + new_y * alpha
                        self.last_locked_output = (final_x, final_y)
                    except:
                        pass
            return self.last_locked_output

        # 重置眨眼锁定标志
        self.blink_lock_active = False

        # 3. 正常映射逻辑
        if self.is_calibrated and hasattr(self, 'poly_matrix'):
            try:
                # 标准化当前坐标
                ex_norm = (eye_pos[0] - self.eye_min[0]) / self.eye_range[0]
                ey_norm = (eye_pos[1] - self.eye_min[1]) / self.eye_range[1]

                # 构造特征 [x, y, x*y, 1]
                feat = np.array([ex_norm, ey_norm, ex_norm * ey_norm, 1])

                # 矩阵映射
                res = self._map_normalized(feat)
                final_x, final_y = res[0], res[1]

                # 边界约束
                final_x = max(0, min(final_x, screen_size[0]))
                final_y = max(0, min(final_y, screen_size[1]))

            except Exception as e:
                print(f"坐标映射计算错误: {e}")
                final_x, final_y = self.last_locked_output
        else:
            # 未校准时的兜底逻辑
            if self.frame_size is None:
                return self.last_locked_output
            frame_width, frame_height = self.frame_size
            final_x = (eye_pos[0] / frame_width) * screen_size[0]
            final_y = (eye_pos[1] / frame_height) * screen_size[1]

        # 4. 更新稳定缓冲区
        actual_final = (float(final_x), float(final_y))
        self.stable_pos_buffer.append(actual_final)
        if len(self.stable_pos_buffer) > 30:  # 保持30帧历史
            self.stable_pos_buffer.pop(0)

        # 更新最后一次稳定的位置快照
        self.last_locked_output = actual_final
        return actual_final

    def reset_recalibration_prompt(self):
        """重置重新校准提示"""
        self.recalibration_prompt = False

    def get_calibration_quality(self):
        """获取校准质量评估"""
        if not self.is_calibrated or len(self.calibration_points) < 3:
            return "POOR"

        error = self._calculate_calibration_error()
        if error < 50:
            return "EXCELLENT"
        elif error < 100:
            return "GOOD"
        elif error < 200:
            return "FAIR"
        else:
            return "POOR"

    def _clear_right_wink_candidate(self):
        self.right_wink_started_at = None
        self.right_wink_last_at = None
        self.right_wink_frames = 0
        self.right_wink_seconds = 0.0
        self.right_wink_confirmed = False
        self.right_wink_release_started_at = None
        self.right_wink_candidate_active = False

    def detect_blink(self, frame):
        """One blink edge per closure; confirm stable reopening before rearming."""
        import time
        current_time = time.time()
        self.last_blink_observation = None

        def clear_reopen_candidate():
            self.blink_reopen_frames = 0
            self.blink_reopen_started_at = None
            self.blink_reopen_last_at = None

        def require_reopen():
            clear_reopen_candidate()
            self.blink_active = False
            self.blink_rearm_required = True

        def confirm_reopen(drop_l, drop_r):
            if max(drop_l, drop_r) > 0.20 + 1e-9:
                clear_reopen_candidate()
                return False
            now = time.monotonic()
            previous = self.blink_reopen_last_at
            if previous is None or not 0 <= now - previous <= 0.25 + 1e-9:
                clear_reopen_candidate()
                self.blink_reopen_started_at = now
            self.blink_reopen_last_at = now
            self.blink_reopen_frames += 1
            elapsed = max(0.0, now - self.blink_reopen_started_at)
            self.last_blink_observation.update(
                reopen_frames=self.blink_reopen_frames,
                reopen_elapsed_seconds=elapsed)
            return self.blink_reopen_frames >= 2 and elapsed + 1e-9 >= 0.03

        if not hasattr(self, "blink_reopen_frames"):
            clear_reopen_candidate()
        generation = getattr(self, "tracking_generation", None)
        if generation != getattr(self, "blink_tracking_generation", generation):
            require_reopen()
        self.blink_tracking_generation = generation

        if hasattr(self, "current_action") and self.current_action == "mouth":
            if current_time - self.last_action_time < self.action_cooldown:
                require_reopen()
                return None

        if not hasattr(self, "calib_count"):
            self.blink_active = False
            self.base_ear_l, self.base_ear_r = 5.0, 5.0
            self.calib_count = 0

        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)
            if not results.multi_face_landmarks:
                require_reopen()
                return None

            face_landmarks = results.multi_face_landmarks[0]
            h, w = frame.shape[:2]

            def get_data(t_idx, b_idx):
                t = np.mean([[face_landmarks.landmark[i].x * w, face_landmarks.landmark[i].y * h] for i in t_idx], axis=0)
                b = np.mean([[face_landmarks.landmark[i].x * w, face_landmarks.landmark[i].y * h] for i in b_idx], axis=0)
                return np.linalg.norm(t - b), (t[1] + b[1]) / 2

            ear_l, center_l = get_data([159, 158], [145, 153])
            ear_r, center_r = get_data([386, 385], [374, 380])
            if not np.all(np.isfinite((ear_l, ear_r, center_l, center_r))):
                raise ValueError("Invalid eyelid geometry")

            if self.calib_count < 30:
                self.base_ear_l, self.base_ear_r = ear_l, ear_r
                self.base_y = (center_l + center_r) / 2
                self.calib_count += 1
                clear_reopen_candidate()
                return None

            if (not np.all(np.isfinite((self.base_ear_l, self.base_ear_r, self.base_y)))
                    or min(self.base_ear_l, self.base_ear_r) <= 0):
                raise ValueError("Invalid eyelid baseline")

            current_y = (center_l + center_r) / 2
            if (current_y - self.base_y) > (h * 0.02):
                require_reopen()
                return None

            drop_l = (self.base_ear_l - ear_l) / self.base_ear_l
            drop_r = (self.base_ear_r - ear_r) / self.base_ear_r
            self.last_blink_observation = {
                "classifier": "EAR", "ear_l": float(ear_l), "ear_r": float(ear_r),
                "base_ear_l": float(self.base_ear_l), "base_ear_r": float(self.base_ear_r),
                "drop_l": float(drop_l), "drop_r": float(drop_r),
                "closed_threshold": 0.30, "reopen_threshold": 0.20,
                "rearm_required": bool(self.blink_rearm_required),
                "reopen_frames": 0, "reopen_elapsed_seconds": 0.0}

            if self.blink_rearm_required or self.blink_active:
                if confirm_reopen(drop_l, drop_r):
                    self.blink_rearm_required = False
                    self.blink_active = False
                    self.last_blink_observation["rearm_required"] = False
                    self.last_blink_observation["reopen_confirmed"] = True
                    clear_reopen_candidate()
                    self.base_ear_l = 0.995 * self.base_ear_l + 0.005 * ear_l
                    self.base_ear_r = 0.995 * self.base_ear_r + 0.005 * ear_r
                    self.base_y = 0.995 * self.base_y + 0.005 * current_y
                return None

            if drop_l > 0.30 or drop_r > 0.30:
                clear_reopen_candidate()
                self.blink_active = True
                if drop_r > (drop_l + 0.20) and drop_l < 0.50:
                    return "right"
                return "double"

            clear_reopen_candidate()
            self.base_ear_l = 0.995 * self.base_ear_l + 0.005 * ear_l
            self.base_ear_r = 0.995 * self.base_ear_r + 0.005 * ear_r
            self.base_y = 0.995 * self.base_y + 0.005 * current_y
            return None
        except Exception:
            self.last_blink_observation = None
            require_reopen()
            return None


    def detect_mouth_movement(self, frame):
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)
            if not results.multi_face_landmarks:
                self.reset_mouth_scroll()
                return None, None, False, None

            face_landmarks = results.multi_face_landmarks[0]
            h, w = frame.shape[:2]

            # 1. 获取参考锚点
            nose = face_landmarks.landmark[1]  # 鼻尖
            l_eye = face_landmarks.landmark[33]  # 物理左外眼角 (用于校准左脸)
            r_eye = face_landmarks.landmark[263]  # 物理右外眼角 (用于校准右脸)
            l_mouth = face_landmarks.landmark[61]  # 物理左嘴角
            r_mouth = face_landmarks.landmark[291]  # 物理右嘴角

            # 2. 计算当前两侧脸的“基础宽度”（眼角到鼻尖）
            # 这是为了抵消转头。头转动时，这个分母会随之缩小
            base_w_l = abs(l_eye.x - nose.x) + 1e-6
            base_w_r = abs(r_eye.x - nose.x) + 1e-6

            # 3. 计算嘴角的“拉伸率”
            # 撇嘴时，嘴角会向外侧（远离鼻尖）移动
            stretch_l = abs(l_mouth.x - nose.x) / base_w_l
            stretch_r = abs(r_mouth.x - nose.x) / base_w_r

            # 4. 动态基准（Neutral Base）
            if not hasattr(self, 'base_stretch_l'):
                self.base_stretch_l, self.base_stretch_r = stretch_l, stretch_r

            # 闭嘴且没大幅度动作时，缓慢校准基准（消除坐姿不正）
            if abs(stretch_l - self.base_stretch_l) < 0.05:
                self.base_stretch_l = 0.98 * self.base_stretch_l + 0.02 * stretch_l
            if abs(stretch_r - self.base_stretch_r) < 0.05:
                self.base_stretch_r = 0.98 * self.base_stretch_r + 0.02 * stretch_r

            # 5. 计算最终偏差
            # 如果左侧拉伸率增加，说明在向左撇嘴
            diff_l = stretch_l - self.base_stretch_l
            diff_r = stretch_r - self.base_stretch_r

            # 6. 张嘴状态保持：启动和停止用不同阈值，避免边界抖动打断滚动。
            upper = face_landmarks.landmark[13]
            lower = face_landmarks.landmark[14]
            mouth_span = abs(r_mouth.x - l_mouth.x)
            mouth_ratio = (lower.y - upper.y) / (mouth_span + 1e-6)
            if mouth_span <= 1e-6 or not np.isfinite(mouth_ratio):
                self.reset_mouth_scroll()
                return None, None, False, None
            self.mouth_open_ratio = float(mouth_ratio)
            threshold = self.mouth_close_threshold if self.mouth_is_open else self.mouth_open_threshold
            self.mouth_is_open = mouth_ratio > threshold

            # 返回 diff_l 和 diff_r，供下一步判断方向
            return diff_l, diff_r, self.mouth_is_open, w * 0.1

        except Exception as e:
            self.reset_mouth_scroll()
            return None, None, False, None

    def update_mouth_direction_and_scroll(self, left_change, right_change, is_mouth_open):
        # left_change 是物理左侧的拉伸偏差
        # right_change 是物理右侧的拉伸偏差

        # 撇嘴通常会使同侧数值显著增大 (> 0.1)
        # 而转头带来的误差经过眼角归一化后，通常会被压制在 0.05 以下
        THRESHOLD = 0.1

        if not is_mouth_open:
            # 物理左嘴角拉伸 -> 向上
            if left_change > THRESHOLD:
                self.mouth_scroll_direction = 1
                #print(f">>> [向上锁定] 左侧拉伸: {left_change:.2f}")

            # 物理右嘴角拉伸 -> 向下
            elif right_change > THRESHOLD:
                self.mouth_scroll_direction = -1
                #print(f">>> [向下锁定] 右侧拉伸: {right_change:.2f}")

        # 滚动执行逻辑
        scroll_command = 0
        if is_mouth_open:
            self.mouth_open_counter = min(self.mouth_open_counter + 1, 5)
            if self.mouth_open_counter >= 3:
                scroll_command = self.mouth_scroll_direction
        else:
            self.mouth_open_counter = 0

        return scroll_command, False

    def reset_mouth_scroll(self):
        """重置嘴部滚动状态（但保留方向设定）"""
        self.mouth_open_counter = 0
        self.cooldown_frames = 0
        self.mouth_is_open = False
        self.mouth_open_ratio = None
        # 注意：不重置 mouth_scroll_direction 和 mouth_center_ref
