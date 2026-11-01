"""Personal calibration, kept separate from hardware and held-out evaluation."""
import math
import time
from pathlib import Path
import json
import numpy as np
from mock_link.calibration import (build_profile, validate_calibration, sha256_id,
    q16_from_float, FEATURE_MIN_Q16, FEATURE_MAX_Q16)

FEATURE_SOURCE = 'iris_eye_axes_v1'
TRAIN_TARGETS = [(x, y) for y in (0.12, 0.5, 0.88) for x in (0.12, 0.5, 0.88)]
VALIDATION_TARGETS = [(0.28,0.28),(0.72,0.28),(0.5,0.38),(0.28,0.72),(0.72,0.72)]

def design(features):
    f = np.asarray(features, dtype=float)
    return np.column_stack((f[:,0], f[:,1], f[:,0]*f[:,1], np.ones(len(f))))

def normalized(raw, contract):
    data = np.asarray(raw, dtype=float)
    value = (data - contract['feature_center']) / contract['feature_scale'] + 0.5
    if not np.all(np.isfinite(value)):
        raise ValueError('眼部特征包含非有限值')
    return value

def encode_features(raw_gx, raw_gy, profile, frame_width=None, frame_height=None):
    c = profile['feature_contract']
    if frame_width is not None and [frame_width,frame_height] != c['camera_size']:
        raise ValueError('摄像头分辨率改变，请重新标定')
    f = normalized([raw_gx,raw_gy],c)
    words = [q16_from_float(float(v)) for v in f]
    if any(not FEATURE_MIN_Q16 <= v <= FEATURE_MAX_Q16 for v in words):
        raise ValueError('注视特征超出已标定范围，请恢复头部位置或重新标定')
    return tuple(words)

def integer_reference(gx_q16, gy_q16, wire):
    """Exact map for bounded v2 inputs; both cross term and pixels round half-away."""
    validate_calibration(wire)
    if not all(FEATURE_MIN_Q16 <= v <= FEATURE_MAX_Q16 for v in (gx_q16,gy_q16)):
        raise ValueError('Feature outside current RTL range')
    product = gx_q16 * gy_q16
    cross = ((product+32768)>>16) if product>=0 else -(((-product)+32768)>>16)
    out = []
    for coef, size in ((wire['a_q16'],wire['width']),(wire['b_q16'],wire['height'])):
        acc = gx_q16*coef[0] + gy_q16*coef[1] + cross*coef[2] + (coef[3] << 16)
        rounded = ((acc+(1<<31)) >> 32) if acc >= 0 else -(((-acc)+(1<<31)) >> 32)
        out.append(max(0,min(size-1,rounded)))
    return tuple(out)

def fit_profile(groups, width=1920, height=1080, camera_size=(640,480), source='camera', geometry=None):
    if len(groups) != 9:
        raise ValueError('需要完整九点标定，不能用缺失点替代')
    features, targets, summaries = [], [], []
    for index, group in enumerate(groups):
        samples = np.asarray(group['features'],dtype=float)
        if samples.ndim != 2 or samples.shape[1] != 2 or len(samples) < 20 or not np.isfinite(samples).all():
            raise ValueError('每个目标需要至少20帧有效新鲜样本')
        target = group['target']
        if not np.allclose(target,TRAIN_TARGETS[index],atol=1e-12,rtol=0):
            raise ValueError('记录目标与实际显示目标顺序不一致')
        med = np.median(samples,axis=0)
        mad = np.median(np.abs(samples-med),axis=0)
        keep = np.all(np.abs(samples-med) <= np.maximum(4.5*mad,0.003),axis=1)
        if int(keep.sum()) < 15:
            raise ValueError('标定样本散布过大，请稳定头部后重试')
        centroid = np.median(samples[keep],axis=0)
        features.append(centroid)
        targets.append([target[0]*(width-1),target[1]*(height-1)])
        summaries.append({'target':list(target),'accepted_frames':int(keep.sum()),
                          'total_frames':len(samples),'median':centroid.tolist(),'mad':mad.tolist()})
    f, y = np.asarray(features),np.asarray(targets)
    span = np.ptp(f,axis=0)
    if np.any(span < 0.012):
        raise ValueError('眼部特征变化不足，无法可靠区分九个目标')
    contract = {'feature_source':FEATURE_SOURCE, 'feature_center':((f.max(0)+f.min(0))/2).tolist(),
        'feature_scale':(span/0.8).tolist(), 'normalization':'(raw-center)/scale+0.5',
        'camera_size':list(camera_size), 'processing_mirror':False,
        'feature_axes':'eye-corner axes oriented image-right and image-down; binocular mean',
        'freshness_ms':250, 'filter':'none_before_mapping',
        'display_space':'web_canvas', 'display_geometry':geometry or {},
        'coefficient_order':['gx','gy','gx*gy','1'], 'q16_rounding':'half-away-from-zero'}
    x = design(normalized(f,contract))
    rank = int(np.linalg.matrix_rank(x))
    condition = float(np.linalg.cond(x))
    if rank != 4 or condition > 1000:
        raise ValueError('标定矩阵退化或病态，请重新采集，不能自动夹紧系数')
    coefficients, _, _, _ = np.linalg.lstsq(x,y,rcond=None)
    a = [q16_from_float(float(v)) for v in coefficients[:,0]]
    b = [q16_from_float(float(v)) for v in coefficients[:,1]]
    wire = build_profile(sha256_id(contract),int(width),int(height),a,b)
    error = x@coefficients-y
    training_rms = float(np.sqrt(np.mean(np.sum(error*error,axis=1))))
    # This is a fit sanity check, not an estimate of held-out gaze accuracy.
    if training_rms > math.hypot(width,height)*0.18:
        raise ValueError('训练目标拟合偏差过大，未启用此标定')
    profile = {'profile_format':'lingmou_personal_v1','source':source,
        'created_pc_unix_s':time.time(), 'feature_contract':contract,'wire':wire,
        'float_coefficients':coefficients.tolist(),'target_samples':summaries,
        'fit':{'rank':rank,'condition':condition,'training_rms_px':training_rms,
               'accuracy_claim':'Training fit only; independent target evaluation required'},
        'validation':None}
    validate_local_profile(profile)
    return profile

def validate_local_profile(profile):
    if profile.get('profile_format') != 'lingmou_personal_v1' or profile.get('source') not in ('camera','synthetic'):
        raise ValueError('不支持的标定文件')
    contract = profile['feature_contract']
    wire = validate_calibration(profile['wire'])
    if sha256_id(contract) != wire['feature_contract_id']:
        raise ValueError('标定特征契约哈希不匹配')
    if contract.get('feature_source') != FEATURE_SOURCE or contract.get('normalization') != '(raw-center)/scale+0.5':
        raise ValueError('特征版本或归一化不匹配')
    center, scale = np.asarray(contract['feature_center']),np.asarray(contract['feature_scale'])
    if center.shape != (2,) or scale.shape != (2,) or not np.isfinite(center).all() or not np.isfinite(scale).all() or np.any(scale<=0):
        raise ValueError('标定归一化参数无效')
    return profile

def save_profile(profile,path):
    validate_local_profile(profile)
    Path(path).write_text(json.dumps(profile,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def evaluate_targets(groups,width,height):
    rows, errors, jitters = [], [], []
    for group in groups:
        values = np.asarray(group['predictions'],dtype=float)
        if len(values)<10:
            raise ValueError('独立评估每个目标至少需要10个有效输出')
        target = np.array([group['target'][0]*(width-1),group['target'][1]*(height-1)])
        radial = np.linalg.norm(values-target,axis=1)
        jitter = float(np.sqrt(np.mean(np.sum((values-values.mean(0))**2,axis=1))))
        errors.extend(radial.tolist());jitters.append(jitter)
        rows.append({'target':group['target'],'valid_frames':len(values),
                     'median_error_px':float(np.median(radial)),
                     'jitter_rms_px':jitter})
    if len(rows)!=len(VALIDATION_TARGETS):
        raise ValueError('独立评估尚未完成')
    return {'kind':'held_out_requested_fixation_targets','targets':rows,
            'median_error_px':float(np.median(errors)), 'p90_error_px':float(np.percentile(errors,90)),
            'rms_error_px':float(np.sqrt(np.mean(np.square(errors)))),
            'mean_target_jitter_rms_px':float(np.mean(jitters)),
            'screen_diagonal_px':math.hypot(width,height),
            'not_clinical_validation':True}

def synthetic_profile():
    groups=[]
    for x,y in TRAIN_TARGETS:
        # Deterministic test fixture, saved and displayed as synthetic only.
        groups.append({'target':[x,y],'features':[[0.40+0.18*x, -0.06+0.15*y] for _ in range(24)]})
    return fit_profile(groups,source='synthetic')
