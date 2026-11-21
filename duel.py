"""人机对局（GUI 按钮驱动，单轮）：normal / arm 两种模式。

模式 normal：点「下一轮」→ T 秒倒计时 → 窗口内最后一个稳定手势为人承诺 → 揭晓。
模式 arm   ：点「下一轮」→ 机器人立刻锁定决策 → 识别到两次 rock「划拳」→
             第三次出手为人承诺 → 揭晓（时间窗口动态到第三下）。

公平性：两种模式都在人出手前锁定 robot 决策；external 策略由 LLM 提前提交承诺。

用法（先开机械手窗口 hand.py view，再开这个）：
  python duel.py                         # 随机策略
  python duel.py --strategy external      # LLM Agent 用 hand.py agent 提交
  python duel.py --seconds 3 --stable 6   # normal 模式的倒计时/稳定阈值
"""
import argparse
import os
import queue
import threading
import time

os.environ["GLOG_minloglevel"] = "3"
import cv2
from PIL import Image

import recognize as R
import strategy as S
import judge as J
import history as H
import round_flow as RF
import arena as A
from scoreboard import Scoreboard

HERE = os.path.dirname(os.path.abspath(__file__))
GESTURE_FILE = os.path.join(HERE, "gesture.txt")
HISTORY_FILE = os.path.join(HERE, "history_snapshot.json")
PENDING_FILE = os.path.join(HERE, "pending_decision.json")


def write_gesture(name):
    with open(GESTURE_FILE, "w", encoding="utf-8") as f:
        f.write(name + "\n")


def dump_history(hist, user_id):
    """把该用户的三层历史快照落盘，供将来 Agent/MCP 读取。"""
    import json
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist.snapshot(user_id), f, ensure_ascii=False, indent=2)


class PlayerState:
    """一个玩家的完整对局状态：独立历史 + 独立决策器 + 独立记分。

    玩家 = (user_id, backend)。切换任一个就是切换玩家：
      - 切后端 → 换历史 + 换决策器（干净对比，不继承旧状态）
      - 多用户 → 各自隔离（为机机对决铺路：每个 agent 占一个 user_id）

    决策器统一是 arena.Player：有状态（fusion 的 tracker / hedge 的专家权重）
    还是无状态（random/stats），对这里完全透明——只调 decide()/observe()。
    """

    def __init__(self, user_id, backend, rounds_per_bout):
        self.user_id = user_id
        self.backend = backend
        self.history = H.History(rounds_per_bout)
        self.robot = A.make_robot(backend)      # 统一决策器，状态在实例内
        self.score = [0, 0, 0]        # [机械手胜, 负, 平]

    def view(self):
        """给决策器的对称视图：opp=人历次出拳，me=机器人历次出拳（不含本轮）。"""
        rounds = [r for r in self.history.round_log if r["user_id"] == self.user_id]
        return {
            "opp": [r["human"] for r in rounds],
            "me": [r["agent"] for r in rounds],
            "mode": "",
        }


def make_score_text(states):
    """按玩家分别记分：显示名 胜-负-平 (胜率)，供 GUI + 日志。"""
    lines = []
    for st in states.values():
        w, l, d = st.score
        total = w + l
        rate = f"{w/total*100:.0f}%" if total else "—"
        name = f"{st.user_id}/{A.robot_display(st.backend)}"
        lines.append(f"{name} {w}-{l}-{d} ({rate})")
    return "  ".join(lines)


def write_pending(hist, user_id):
    """点「下一轮」时落盘待决策请求：Agent 读它来推理出拳。

    只在 external 策略下有意义（random 用不到），但一律写，方便 MCP 观察。
    """
    import json
    payload = dict(hist.snapshot(user_id))
    payload.update({"ts": time.time(), "round_hint": len(hist.round_log) + 1})
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_frame(cap, landmarker, flip=True):
    """读一帧 → 识别，返回 (显示帧PIL, 手势名, landmarks)。不 imshow。"""
    ok, frame = cap.read()
    if not ok:
        return None, None, None
    if flip:
        frame = cv2.flip(frame, 1)
    g, lm = R.recognize_frame(landmarker, frame)
    disp = R.draw(frame.copy(), g, lm)
    pil = Image.fromarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB))
    return pil, (g[0] if g else None), lm


def ctx_for(hist, user_id, mode):
    """给决策层的 context：该用户历次出拳序列（不含本轮）+ 模式。"""
    return {
        "history": [r["human"] for r in hist.round_log if r["user_id"] == user_id],
        "mode": mode,
    }


class _DecisionThread(threading.Thread):
    """后台决策线程：点「下一轮」即启动，与人出拳并行。

    入参是一个零参 callable（绑定了 PlayerState.view() 的快照），线程只管调用。
    这样它不关心后端是无状态函数还是有状态对象——统一由调用方绑定。
    """

    def __init__(self, decide_fn):
        super().__init__(daemon=True)
        self.decide_fn = decide_fn
        self.move, self.error = None, None
        self._done = threading.Event()

    def run(self):
        try:
            self.move = self.decide_fn()
        except Exception as e:
            self.error = e
        finally:
            self._done.set()

    def done(self):
        """非阻塞：决策是否已经算完。用于「承诺先到」时的报障判断。"""
        return self._done.is_set()

    def result(self, timeout=120):
        if not self._done.wait(timeout):
            print(f"[decision] 等待超时 {timeout}s", flush=True)
            return None
        if self.error is not None:
            print(f"[decision] 失败: {type(self.error).__name__} {self.error}", flush=True)
            return None
        return self.move


def game_loop(args, gui, hist_unused):
    if args.strategy:          # 只有显式指定时才校验；否则用 GUI 下拉
        A.validate_robot(args.strategy)
    # 玩家注册表：(user_id, backend) -> PlayerState，各自独立历史/权重/记分。
    # 切后端 = 换玩家 = 换历史，干净对比；多用户各自隔离，为机机对决铺路。
    players = {}
    landmarker = R.make_landmarker()
    cap = cv2.VideoCapture(args.index)
    if not cap.isOpened():
        gui.post(("status", f"摄像头 index {args.index} 打不开"))
        gui.post(("busy", False))
        return

    try:
        while True:
            try:
                cmd = gui.commands.get(timeout=0.1)
            except queue.Empty:
                continue
            kind = cmd[0]
            if kind == "quit":
                break
            if kind != "start":
                continue

            mode = cmd[1]
            user_id = cmd[2] if len(cmd) > 2 else "anonymous"
            strategy = cmd[3] if len(cmd) > 3 else args.strategy

            # 取（或新建）该玩家状态：切后端 = 换历史，干净对比
            key = (user_id, strategy)
            if key not in players:
                players[key] = PlayerState(user_id, strategy, args.rounds_per_bout)
                print(f"[玩家] 新建 {user_id}/{strategy}（独立历史+权重+记分）", flush=True)
            player = players[key]
            hist = player.history

            write_pending(hist, user_id)   # 落盘待决策请求，供 MCP/Agent 观察
            gui.post(("busy", True))

            # ── 决策在点「下一轮」瞬间就启动，与人出拳并行 ──
            # view 快照在启动时取（不含本轮），保证公平性；决策器实例状态在 observe 时更新。
            view_snapshot = player.view()
            decision = _DecisionThread(lambda: player.robot.decide(view_snapshot))
            decision.start()

            # 心理战：在出拳窗口开始时说一句（只用历史，不进决策）
            if A.is_taunting(strategy) and hasattr(player.robot, "talk"):
                try:
                    gui.post(("taunt", player.robot.talk(view_snapshot)))
                except Exception as e:
                    print(f"[taunt] 话术生成失败: {e}", flush=True)
            else:
                gui.post(("taunt", ""))

            flow = RF.make_flow(mode, gui, cap=cap, landmarker=landmarker,
                                seconds=args.seconds, stable=args.stable,
                                recognize=read_frame)
            human, _meta = flow.wait_commit()      # 阻塞到人的承诺（或超时/取消）

            # ── 汇聚点：人已承诺，检查决策是否已就绪 ──
            agent = None
            if human is not None:
                if not decision.done():
                    # 报障：人比模型快。给一个短宽限（秒级预算内），并显式告知。
                    gui.post(("status", "太快了，模型没来得及决策——等它一下…"))
                    gui.post(("too_fast", True))
                    if decision.result(timeout=1.5) is None:
                        gui.post(("status", "模型仍未返回，本轮跳过"))
                        human = None      # 撤销本轮，不揭晓
                    else:
                        agent = decision.move
                        gui.post(("too_fast", False))
                else:
                    agent = decision.result(timeout=0.1)
            flow.on_decided(agent)

            if human is not None and agent is not None:
                result = J.judge(human, agent)
                # 决策器在揭晓后更新自己的内部状态（有状态后端据此学习）
                player.robot.observe(agent, human, A.outcome(agent, human))
                # 记入该玩家自己的记分（机械手视角：胜/负/平）
                s = player.score
                if result == "agent":
                    s[0] += 1
                elif result == "human":
                    s[1] += 1
                else:
                    s[2] += 1
                score_text = make_score_text(players)
                gui.post(("per_backend", score_text))
                rec = hist.add_round(human, agent, result, mode, user_id)  # 记入该玩家三层历史
                dump_history(hist, user_id)   # 落盘该用户三层快照，供 Agent/MCP 读取
                if os.path.exists(PENDING_FILE):
                    os.remove(PENDING_FILE)   # 本轮已揭晓，清掉待决策请求
                write_gesture(agent)     # 揭晓瞬间：机械手出 agent 的拳
                gui.post(("reveal", human, agent, result,
                          f"累计 {len(hist.round_log)} 轮 / {len(hist.get_bouts())} 局",
                          rec['bout'], rec['round'],
                          (rec['round'] - 1) % hist.rounds_per_bout + 1,
                          hist.rounds_per_bout))
                if A.is_taunting(strategy):
                    gui.post(("taunt", ""))   # 揭晓后收起话术
                print(f"[{user_id}] 局{rec['bout']} 轮{rec['round']} 模式={mode} "
                      f"人={J.ZH[human]} agent={J.ZH[agent]} → {J.RESULT_ZH[result]}", flush=True)
                print(f"[记分] {score_text}", flush=True)
            # 无论揭晓与否，进入下一轮前确保决策线程已结束，避免状态竞争
            if not decision.done():
                decision.result(timeout=1.5)
            time.sleep(0.5)
            gui.post(("busy", False))
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="石头剪刀布人机对局（GUI 按钮驱动）")
    ap.add_argument("--strategy", default=None,
                    help="默认决策后端（不填则用 GUI 下拉的选择）；"
                         "可选: " + ", ".join(b for b, _ in A.robot_choices()))
    ap.add_argument("--seconds", type=int, default=3, help="normal 模式倒计时秒数")
    ap.add_argument("--stable", type=int, default=6, help="连续 N 帧稳定才认定")
    ap.add_argument("--index", type=int, default=0, help="摄像头序号")
    ap.add_argument("--rounds-per-bout", type=int, default=H.ROUNDS_PER_BOUT,
                    help="每局轮数（默认 5）")
    args = ap.parse_args()

    hist = H.History(rounds_per_bout=args.rounds_per_bout)
    gui = Scoreboard()
    t = threading.Thread(target=game_loop, args=(args, gui, hist), daemon=True)
    t.start()
    gui.run()


if __name__ == "__main__":
    main()
