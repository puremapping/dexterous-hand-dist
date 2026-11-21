"""Aero Hand Open 仿真手：常驻窗口 + 命令行换手势。

用法：
  开窗口（一次）：    uv run python hand.py view [初始手势]
  换手势（随时）：    uv run python hand.py play rock|paper|scissors

  view 开一次就常驻；play 只写 gesture.txt、立即退出，窗口实时跟着变。
  窗口操作：拖拽转视角、滚轮缩放、右键平移。关窗口才退出。
"""
import argparse
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GESTURE_FILE = os.path.join(HERE, "gesture.txt")
AGENT_COMMIT_FILE = os.path.join(HERE, "agent_commit.txt")
GESTURE_NAMES = ("rock", "paper", "scissors")   # CLI 用户接口；控制映射在 gestures.py


def read_gesture():
    try:
        with open(GESTURE_FILE, encoding="utf-8") as f:
            return f.read().strip().lower()
    except OSError:
        return None


def cmd_play(args):
    """只写 gesture.txt，不加载模型——换手势要快。"""
    with open(GESTURE_FILE, "w", encoding="utf-8") as f:
        f.write(args.gesture + "\n")
    print(f"出拳：{args.gesture}")


def cmd_agent(args):
    """LLM Agent 提交本回合出拳承诺（external 策略消费）。

    和 play 的区别：写 agent_commit.txt 而非 gesture.txt——
    承诺先锁定、不立刻亮给机械手，等裁判揭晓时才生效，保证公平。
    """
    with open(AGENT_COMMIT_FILE, "w", encoding="utf-8") as f:
        f.write(args.gesture + "\n")
    print(f"Agent 承诺已提交：{args.gesture}（external 策略会在揭晓时消费）")


def cmd_view(args):
    import mujoco
    import mujoco.viewer
    import gestures as G

    name = args.gesture or read_gesture() or "paper"
    if name not in GESTURE_NAMES:
        name = "paper"
    model, data, home = G.load_model()
    G.apply_gesture(data, home, name)
    print(f"常驻窗口打开，当前={name}。用 `hand.py play <手势>` 换。", flush=True)

    with mujoco.viewer.launch_passive(model, data) as viewer:
        viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        v = G.CAM
        viewer.cam.lookat[:] = v["lookat"]
        viewer.cam.distance = v["distance"]
        viewer.cam.azimuth = v["azimuth"]
        viewer.cam.elevation = v["elevation"]
        last = name
        while viewer.is_running():
            g = read_gesture()
            if g and g != last and g in GESTURE_NAMES:
                G.apply_gesture(data, home, g)
                last = g
                print(f"换手势 -> {g}", flush=True)
            mujoco.mj_step(model, data)
            viewer.sync()
            time.sleep(0.01)


def main():
    p = argparse.ArgumentParser(description="Aero Hand Open 仿真手")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("view", help="打开常驻窗口（一次）")
    v.add_argument("gesture", nargs="?", choices=GESTURE_NAMES, help="初始手势")
    v.set_defaults(fn=cmd_view)

    s = sub.add_parser("play", help="换手势（写 gesture.txt，立即退出）")
    s.add_argument("gesture", choices=GESTURE_NAMES, help="要出的手势")
    s.set_defaults(fn=cmd_play)

    a = sub.add_parser("agent", help="LLM Agent 提交本回合出拳承诺（external 策略用）")
    a.add_argument("gesture", choices=GESTURE_NAMES, help="Agent 决定出的拳")
    a.set_defaults(fn=cmd_agent)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
