"""从摄像头识别石头/剪刀/布。

用法：
  python recognize.py              实时窗口，按 q 退出，空格拍照识别
  python recognize.py --once       拍一张立即识别并打印，不开窗口

识别法：MediaPipe HandLandmarker 拿 21 关键点，数伸直的手指。
  rock=0 根伸直，scissors=食指+中指，paper=4 根以上。
"""
import argparse
import os

os.environ["GLOG_minloglevel"] = "3"   # 压掉 MediaPipe 的 INFO/WARNING 刷屏
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "model", "hand_landmarker.task")

# 手指：(tip, pip, mcp) 关键点索引。拇指特殊，用 tip 到 index_mcp 的距离判。
FINGERS = {
    "index":  (8, 6, 5),
    "middle": (12, 10, 9),
    "ring":   (16, 14, 13),
    "pinky":  (20, 18, 17),
}


def make_landmarker():
    opts = mp_vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
    )
    return mp_vision.HandLandmarker.create_from_options(opts)


def classify(landmarks):
    """数伸直的手指，返回 (手势, 每根手指是否伸直的 dict)。"""
    def dist(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    wrist = landmarks[0]
    index_mcp = landmarks[5]
    extended = {}
    for name, (tip, pip, mcp) in FINGERS.items():
        t, p, m = landmarks[tip], landmarks[pip], landmarks[mcp]
        # 指尖到手腕距离 > 中间关节到手腕距离 * 1.1 → 伸直
        extended[name] = dist(t, wrist) > dist(p, wrist) * 1.10
    # 拇指：指尖到小指 MCP 的距离 > 拇指 IP 到小指 MCP 的距离 → 张开
    thumb_open = dist(landmarks[4], landmarks[17]) > dist(landmarks[3], landmarks[17]) * 1.05
    extended["thumb"] = thumb_open

    n = sum(extended.values())
    # 剪刀：食指+中指伸直，无名+小指弯曲（拇指随意）
    if extended["index"] and extended["middle"] and not extended["ring"] and not extended["pinky"]:
        return "scissors", extended
    if n >= 4:
        return "paper", extended
    if n <= 1:
        return "rock", extended
    return "unknown", extended


def recognize_frame(landmarker, bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_img)
    if not result.hand_landmarks:
        return None, None
    return classify(result.hand_landmarks[0]), result.hand_landmarks[0]


def draw(frame, gesture, landmarks):
    if landmarks:
        for i, lm in enumerate(landmarks):
            x, y = int(lm.x * frame.shape[1]), int(lm.y * frame.shape[0])
            cv2.circle(frame, (x, y), 4, (0, 255, 0), -1)
    label = gesture[0] if gesture else "no hand"
    cv2.putText(frame, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
    return frame


def main():
    ap = argparse.ArgumentParser(description="摄像头识别石头剪刀布")
    ap.add_argument("--once", action="store_true", help="拍一张立即识别")
    ap.add_argument("--index", type=int, default=0, help="摄像头序号")
    args = ap.parse_args()

    landmarker = make_landmarker()
    cap = cv2.VideoCapture(args.index)
    if not cap.isOpened():
        cap.release()
        raise SystemExit(f"摄像头 index {args.index} 打不开")

    if args.once:
        ok, frame = cap.read()
        if not ok:
            cap.release()
            raise SystemExit("拍照失败")
        frame = cv2.flip(frame, 1)
        gesture, lm = recognize_frame(landmarker, frame)
        out = os.path.join(HERE, "recognize_once.jpg")
        cv2.imwrite(out, draw(frame.copy(), gesture, lm))
        cap.release()
        if gesture:
            name = {"rock": "石头", "paper": "布", "scissors": "剪刀", "unknown": "未识别"}[gesture[0]]
            print(f"识别：{name}  {gesture[0]}  手指={gesture[1]}")
        else:
            print("没检测到手")
        print(f"截图：{out}")
        return

    print("实时识别：窗口里看结果，q 退出。镜像画面。", flush=True)
    last = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)
        gesture, lm = recognize_frame(landmarker, frame)
        if gesture and gesture[0] != "unknown":
            last = gesture[0]
            print(f"识别：{gesture[0]}  手指={gesture[1]}", flush=True)
        cv2.imshow("RPS recognize", draw(frame, gesture, lm))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
