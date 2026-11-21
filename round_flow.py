"""三种模式的统一适配器：机器人方只管「拿到承诺 → 决策」，模式差异收在这里。

接口：
    flow = make_flow(mode, gui, cap, landmarker, args)
    human, meta = flow.wait_commit()      # 阻塞到人的承诺就绪；None=本轮取消/超时
    if human is not None:
        agent = S.decide(strategy, {"history":..., "mode": mode})   # 统一决策点
        flow.on_decided(agent)            # 各模式自己的收尾（如 robot_ready 推送）

「何时可以开始决策」的判定完全由各 Flow 子类封装——对局循环（game_loop）
不再有 if mode == ... 分支，也不再知道识别细节。
"""
import time

import strategy as S


def ctx_for(hist, user_id, mode, tracker=None):
    """决策层 context：该用户历次出拳（不含本轮）+ 我方历次出拳 + 模式 + 自适应权重。

    my_moves 很重要：对手可能模仿或反制我上一手，不含它就无法预测这类行为。
    tracker 是每个玩家独立的准确率追踪器（机机对决时各用各的）。
    """
    rounds = [r for r in hist.round_log if r["user_id"] == user_id]
    return {
        "history": [r["human"] for r in rounds],
        "my_moves": [r["agent"] for r in rounds],
        "mode": mode,
        "tracker": tracker,
    }


class _BaseFlow:
    """公共部分：持有 gui / 决策所需上下文的钩子。"""

    mode = "?"

    def __init__(self, gui):
        self.gui = gui

    def wait_commit(self):
        raise NotImplementedError

    def on_decided(self, agent):
        if agent is not None:
            self.gui.post(("robot_ready", agent))

    def cancelled(self):
        return None, None


class CameraTimedFlow(_BaseFlow):
    """普通模式：倒计时窗内持续识别，取「最后一个稳定手势」为承诺。"""

    mode = "normal"

    def __init__(self, gui, cap, landmarker, seconds, stable, recognize):
        super().__init__(gui)
        self.cap, self.landmarker = cap, landmarker
        self.seconds, self.stable, self.recognize = seconds, stable, recognize

    def wait_commit(self):
        self.gui.post(("status",
                       f"{self.seconds} 秒内出拳并保持（取最后稳定手势）；Agent 可同时提交决策。"))
        deadline = time.monotonic() + self.seconds
        human, candidate, streak = None, None, 0
        while time.monotonic() < deadline:
            pil, moved, lm = self.recognize(self.cap, self.landmarker)
            if pil is None:
                break
            self.gui.post(("frame", pil))
            if moved in S.MOVES:
                streak = streak + 1 if moved == candidate else 1
                candidate = moved
                if streak >= self.stable:
                    human = moved            # 持续刷新，取最后一个稳定值
            else:
                candidate, streak = None, 0
            remain = max(0, int(deadline - time.monotonic()) + 1)
            self.gui.post(("countdown", remain))
            time.sleep(0.03)
        return (human, {"recognized": human}) if human else (None, None)


class CameraArmFlow(_BaseFlow):
    """划两下模式：开场 no-hand → ✊ → no-hand → ✊ → no-hand → 承诺手势。

    每道闸门之间都要求「手离开画面」至少 nohand_frames 帧，防止：
      开场手就在画面里被当成第一次划拳 / 一次慢出拳被数成两次 /
      第二次划拳的余势被当成承诺。
    """

    mode = "arm"

    def __init__(self, gui, cap, landmarker, stable, recognize,
                 arm_threshold=2, nohand_frames=5, guard_seconds=60):
        super().__init__(gui)
        self.cap, self.landmarker = cap, landmarker
        self.stable, self.recognize = stable, recognize
        self.arm_threshold, self.nohand_frames = arm_threshold, nohand_frames
        self.guard = guard_seconds

    def wait_commit(self):
        self.gui.post(("status",
                       "先把手拿出画面（开场判定），再做两次 ✊ 划拳，第三次出手为承诺。"))
        arm_count = 0
        phase = "arm"           # arm: 等划拳；move: 等承诺手势
        need_nohand = True      # 开场也要求一次 no hand
        nohand_seen = 0
        candidate, streak = None, 0
        deadline = time.monotonic() + self.guard

        while time.monotonic() < deadline:
            pil, moved, lm = self.recognize(self.cap, self.landmarker)
            if pil is None:
                break
            self.gui.post(("frame", pil))

            if moved is None or moved == "unknown":
                nohand_seen += 1
                candidate, streak = None, 0
                if need_nohand and nohand_seen >= self.nohand_frames:
                    need_nohand = False
                    if phase == "move":
                        hint = "收手确认，现在出承诺拳（石头/剪刀/布）"
                    elif arm_count == 0:
                        hint = "开场确认，可以开始划拳了"
                    else:
                        hint = "收到收手，可以出下一拳"
                    self.gui.post(("status", hint))
            else:
                nohand_seen = 0
                streak = streak + 1 if moved == candidate else 1
                candidate = moved

                if streak >= self.stable and not need_nohand:
                    if phase == "arm":
                        if moved == "rock":
                            arm_count += 1
                            self.gui.post(("arm", arm_count))
                            need_nohand = True
                            candidate, streak = None, 0
                            if arm_count >= self.arm_threshold:
                                phase = "move"
                                need_nohand = True     # 第二次划拳后同样要收手
                                self.gui.post(("status", "划拳完成，先收手，再出你的拳（石头/剪刀/布）"))
                                candidate, streak = None, 0
                    else:  # phase move: 承诺拳
                        return moved, {"phase": "move"}
            time.sleep(0.03)

        self.gui.post(("status", "超时或没识别到承诺手势，本局跳过"))
        return None, None


class ButtonFlow(_BaseFlow):
    """按钮模式：不碰摄像头，等 GUI 发来的 human_move 命令。"""

    mode = "button"

    def __init__(self, gui, guard_seconds=120):
        super().__init__(gui)
        self.guard = guard_seconds

    def wait_commit(self):
        import queue
        self.gui.post(("status", "按钮模式：点石头/剪刀/布出手；Agent 可同时提交决策。"))
        deadline = time.monotonic() + self.guard
        while time.monotonic() < deadline:
            try:
                cmd = self.gui.commands.get(timeout=0.1)
            except queue.Empty:
                continue
            if cmd and cmd[0] == "human_move":
                return cmd[1], {"clicked": True}
            if cmd and cmd[0] == "quit":
                return None, None
        self.gui.post(("status", "按钮模式超时，本轮跳过"))
        return None, None


def make_flow(mode, gui, cap=None, landmarker=None, seconds=3, stable=6, recognize=None):
    if mode == "button":
        return ButtonFlow(gui)
    if mode == "arm":
        return CameraArmFlow(gui, cap, landmarker, stable, recognize)
    return CameraTimedFlow(gui, cap, landmarker, seconds, stable, recognize)
