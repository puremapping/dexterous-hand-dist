"""Aero Hand Open 手势定义的唯一真相源。

控制方向（probe_controls.py 实测扫出）：
  关节角 0=伸直，1.57=弯曲；手指肌腱 ctrl 越大越伸直。
  4 根手指各一根独立肌腱 + 拇指 3 路，共 7 个执行器。
"""
import os
import mujoco

HERE = os.path.dirname(os.path.abspath(__file__))
XML = os.path.join(HERE, "model", "mujoco", "scene_right.xml")

# ctrl 端点
F_STRAIGHT, F_CURL = 0.1104, 0.0585   # 手指肌腱
T_OPEN, T_TUCK = 1.70, 0.30           # 拇指外展
T_EXT, T_CURL = 0.0384, 0.0262        # 拇指 CMC 屈
T2_EXT, T2_CURL = 0.1121, 0.0816      # 拇指 MCP/IP

# 顺序：[index, middle, ring, pinky, thumb_abd, thumb_th1, thumb_th2]
GESTURES = {
    "rock":     [F_CURL,     F_CURL,     F_CURL,     F_CURL,     T_TUCK, T_CURL, T2_CURL],
    "paper":    [F_STRAIGHT, F_STRAIGHT, F_STRAIGHT, F_STRAIGHT, T_OPEN, T_EXT,  T2_EXT],
    "scissors": [F_STRAIGHT, F_STRAIGHT, F_CURL,     F_CURL,     T_TUCK, T_CURL, T2_CURL],
}

# 正对掌面的相机，三种手势都看得清
CAM = dict(lookat=(0.15, 0.0, 0.0), distance=0.42, azimuth=90.0, elevation=75.0)


def load_model():
    model = mujoco.MjModel.from_xml_path(XML)
    data = mujoco.MjData(model)
    return model, data, model.key("home")


def apply_gesture(data, home, name):
    """把 data 摆成指定手势（不步进，调用方自己 step）。"""
    data.qpos[:] = home.qpos
    data.qvel[:] = 0
    data.ctrl[:] = GESTURES[name]

