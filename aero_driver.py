"""Aero Hand Open 真机驱动：读 gesture.txt，把 RPS 手势翻译成 SDK 执行。

和 hand.py（MuJoCo 仿真）读同一个 gesture.txt，二者可切换、互不干扰：
  - 仿真：uv run python hand.py view
  - 真机：uv run python aero_driver.py [--port COM3] [--dry-run]

无实物/没装 SDK 时自动 dry-run：只打印将发的 pose，不连接、不报错。
插上手并装好 aero-open-sdk 后用 --port 指定 COM 口即可真机执行。

手势 pose 取自 SDK 官方 examples（非官方 RPS 定义，实机需 Homing 后微调）：
  paper    = Open Palm    [100, 0,  0,  0,  0,  0,  0]
  scissors = Peace Sign   [90,  0,  0,  0,  0,  90, 90]
  rock     = Power Grasp  [100, 55, 30, 60, 60, 60, 60]
通道顺序（SDK 0..6）：拇指外展 / 拇指屈曲 / 拇指肌腱 / 食指 / 中指 / 无名指 / 小指
"""
import argparse
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GESTURE_FILE = os.path.join(HERE, "gesture.txt")

# SDK actuation 值：0=全伸, 100=全握（来自 aero-open-sdk examples）
POSES = {
    "paper":    [100.0, 0.0,  0.0,  0.0,  0.0,  0.0,  0.0],
    "scissors": [90.0,  0.0,  0.0,  0.0,  0.0,  90.0, 90.0],
    "rock":     [100.0, 55.0, 30.0, 60.0, 60.0, 60.0, 60.0],
}


def read_gesture():
    try:
        with open(GESTURE_FILE, encoding="utf-8") as f:
            return f.read().strip().lower()
    except OSError:
        return None


def make_hand(port, speed, torque, dry_run):
    """返回 (hand, is_dry_run)。SDK 不可用或 dry_run 时 hand=None。"""
    if dry_run:
        return None, True
    try:
        from aero_open_sdk.aero_hand import AeroHand
    except ImportError as e:
        print(f"[aero_driver] 未装 aero-open-sdk（{e}），自动 dry-run。"
              f"装法：pip install aero-open-sdk", flush=True)
        return None, True
    kw = {"port": port} if port else {}
    hand = AeroHand(**kw)
    for i in range(7):
        hand.set_speed(i, speed)
        hand.set_torque(i, torque)
    return hand, False


def apply(hand, gesture, dry_run):
    """把一次手势执行到底。返回是否真的动了手。"""
    pose = POSES.get(gesture)
    if pose is None:
        return False
    if dry_run:
        print(f"[aero_driver] DRY-RUN → {gesture:8} actuations={pose}", flush=True)
        return False
    hand.set_actuations(pose)
    return True


def main():
    ap = argparse.ArgumentParser(description="Aero Hand Open 真机驱动（读 gesture.txt）")
    ap.add_argument("--port", default=os.environ.get("AERO_PORT", ""),
                    help="串口，如 COM3；留空则 SDK 自动检测")
    ap.add_argument("--speed", type=int, default=80, help="速度 0..32766（默认 80）")
    ap.add_argument("--torque", type=int, default=400, help="力矩 0..1000（默认 400）")
    ap.add_argument("--poll", type=float, default=0.05, help="轮询 gesture.txt 间隔秒")
    ap.add_argument("--dry-run", action="store_true", help="不连接真机，只打印")
    args = ap.parse_args()

    hand, dry = make_hand(args.port, args.speed, args.torque, args.dry_run)
    print(f"[aero_driver] 启动：{'DRY-RUN' if dry else '真机 port=' + (args.port or 'auto')}"
          f"  监听 {GESTURE_FILE}", flush=True)

    last = None
    try:
        while True:
            g = read_gesture()
            if g != last:
                if g in POSES:
                    apply(hand, g, dry)
                last = g
            time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        if hand is not None:
            hand.set_actuations([100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # 回张开位
            hand.close()
            print("[aero_driver] 已回张开位并关闭连接", flush=True)


if __name__ == "__main__":
    main()
