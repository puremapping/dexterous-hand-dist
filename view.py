"""交互式查看 Aero Hand Open 模型。

窗口操作：
  左键拖拽 = 转视角    滚轮 = 缩放    右键拖拽 = 平移
  方向键 ← → = 切换模型自带相机

手势切换：改同目录 gesture.txt 的内容为 rock / paper / scissors，窗口实时换姿态。
"""
import os
import time
import mujoco
import mujoco.viewer

HERE = os.path.dirname(os.path.abspath(__file__))
XML = os.path.join(HERE, "model", "mujoco", "scene_right.xml")
GESTURE_FILE = os.path.join(HERE, "gesture.txt")

F_STRAIGHT, F_CURL = 0.1104, 0.0585
T_OPEN, T_TUCK = 1.70, 0.30
T_EXT, T_CURL = 0.0384, 0.0262
T2_EXT, T2_CURL = 0.1121, 0.0816

GESTURES = {
    "rock":     [F_CURL,     F_CURL,     F_CURL,     F_CURL,     T_TUCK, T_CURL, T2_CURL],
    "paper":    [F_STRAIGHT, F_STRAIGHT, F_STRAIGHT, F_STRAIGHT, T_OPEN, T_EXT,  T2_EXT],
    "scissors": [F_STRAIGHT, F_STRAIGHT, F_CURL,     F_CURL,     T_TUCK, T_CURL, T2_CURL],
}

model = mujoco.MjModel.from_xml_path(XML)
data = mujoco.MjData(model)
home = model.key("home")

def current_gesture():
    try:
        with open(GESTURE_FILE, encoding="utf-8") as f:
            return f.read().strip().lower()
    except OSError:
        return "paper"

def apply_gesture(name):
    data.qpos[:] = home.qpos
    data.qvel[:] = 0
    data.ctrl[:] = GESTURES[name]
    print(f"[viewer] gesture -> {name}", flush=True)

g0 = current_gesture()
apply_gesture(g0 if g0 in GESTURES else "paper")

with mujoco.viewer.launch_passive(model, data) as viewer:
    # 初始视角：正对掌面，便于看清手指
    viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    viewer.cam.lookat[:] = [0.15, 0.0, 0.0]
    viewer.cam.distance = 0.42
    viewer.cam.azimuth = 90.0
    viewer.cam.elevation = 75.0

    last = g0
    print("[viewer] opened. drag to rotate, scroll to zoom.", flush=True)
    while viewer.is_running():
        g = current_gesture()
        if g != last and g in GESTURES:
            apply_gesture(g)
            last = g
        mujoco.mj_step(model, data)
        viewer.sync()
        time.sleep(0.01)
