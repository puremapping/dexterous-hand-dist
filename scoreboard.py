"""计分板 GUI（tkinter，主线程）：按钮控制下一轮、模式选择、倒计时/结果/比分/摄像头预览。

和工作线程只通过两个 queue 通信：
  commands（GUI→worker）: ("start", mode, user_id) / ("human_move", move) / ("quit",)
  events（worker→GUI）  : ("countdown", n|str) / ("status", s) /
                          ("arm", count) / ("committed", human, agent) /
                          ("reveal", human, agent, result, score_str) /
                          ("frame", PIL.Image) / ("busy", bool) / ("robot_ready", move)
"""
import queue
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

ZH = {"rock": "✊ 石头", "paper": "✋ 布", "scissors": "✌ 剪刀", None: "—"}
RESULT_ZH = {"human": "你赢了！", "agent": "机械手赢了", "draw": "平局"}
MODE_ZH = {
    "normal": "普通：倒计时内出手",
    "arm": "划两下：✊✊后第三下为承诺",
    "button": "按钮：点石头/剪刀/布出手",
}


class Scoreboard:
    def __init__(self, title="石头剪刀布 · 人机对局"):
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("700x1080")
        self.root.resizable(False, False)
        self.events = queue.Queue()      # worker -> GUI
        self.commands = queue.Queue()    # GUI -> worker
        self._photo = None               # 持有引用，防 GC

        pad = dict(padx=10, pady=6)

        # 登录区
        import auth as A
        self._auth = A
        self.user_id = None
        login_bar = ttk.Frame(self.root)
        login_bar.pack(fill="x", **pad)
        ttk.Label(login_bar, text="用户：").pack(side="left")
        self.user_entry = ttk.Entry(login_bar, width=12)
        self.user_entry.pack(side="left", padx=4)
        ttk.Label(login_bar, text="密码：").pack(side="left")
        self.pwd_entry = ttk.Entry(login_bar, width=12, show="*")
        self.pwd_entry.pack(side="left", padx=4)
        ttk.Button(login_bar, text="登录", command=self.do_login).pack(side="left", padx=4)
        ttk.Button(login_bar, text="注册", command=self.do_register).pack(side="left", padx=4)
        self.user_label = tk.StringVar(value="未登录")
        tk.Label(login_bar, textvariable=self.user_label, fg="#2563eb").pack(side="left", padx=8)

        # 倒计时 / 大字
        self.big = tk.StringVar(value="准备")
        self.big_label = tk.Label(self.root, textvariable=self.big,
                                  font=("Consolas", 36, "bold"), fg="#2563eb")
        self.big_label.pack(**pad)

        # 摄像头预览（尺寸由图片决定，按窗口宽度缩放）
        self.cam_label = tk.Label(self.root, text="摄像头未启动",
                                  bg="#111", fg="#888")
        self.cam_label.pack(**pad)

        # 双方出拳
        row = ttk.Frame(self.root)
        row.pack(**pad)
        tk.Label(row, text="你", font=("Consolas", 14, "bold")).grid(row=0, column=0, **pad)
        tk.Label(row, text="机械手", font=("Consolas", 14, "bold")).grid(row=0, column=1, **pad)
        self.human_var = tk.StringVar(value=ZH[None])
        self.agent_var = tk.StringVar(value=ZH[None])
        tk.Label(row, textvariable=self.human_var, font=("Consolas", 20),
                 width=11, relief="ridge").grid(row=1, column=0, **pad)
        tk.Label(row, textvariable=self.agent_var, font=("Consolas", 20),
                 width=11, relief="ridge").grid(row=1, column=1, **pad)

        # 裁判结果 + 比分
        self.result = tk.StringVar(value="")
        self.result_label = tk.Label(self.root, textvariable=self.result,
                                     font=("Consolas", 24, "bold"), fg="#dc2626")
        self.result_label.pack(**pad)
        # 心理战话术气泡（机器人对人说的话；非心理战后端为空）
        self.taunt = tk.StringVar(value="")
        self.taunt_label = tk.Label(self.root, textvariable=self.taunt,
                                    font=("Consolas", 13, "italic"), fg="#7c3aed",
                                    wraplength=620, justify="center")
        self.taunt_label.pack(**pad)
        self.score = tk.StringVar(value="累计 0 轮 / 0 局")
        tk.Label(self.root, textvariable=self.score, font=("Consolas", 16)).pack(**pad)
        # 按后端分别记分（机械手视角），便于对比「融合 vs 统计」等
        self.per_backend = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.per_backend, font=("Consolas", 9),
                 fg="#1d4ed8", wraplength=640, justify="center").pack(**pad)

        # 状态
        self.status = tk.StringVar(value="选模式，点「下一轮」开始")
        self.status_label = tk.Label(self.root, textvariable=self.status,
                                     font=("Consolas", 10), fg="#555", wraplength=620)
        self.status_label.pack(**pad)

        # 决策后端选择（统一注册点 arena.robot_choices）
        import arena as _A
        backend_bar = ttk.Frame(self.root)
        backend_bar.pack(padx=10, pady=4, before=self.big_label)   # 置顶，按钮不被预览挤出
        ttk.Label(backend_bar, text="决策后端：").pack(side="left")
        choices = _A.robot_choices()
        self.backend_label = {text: bid for bid, text in choices}
        # 默认选 hedge（实测综合最强 77%）；没有就退回第一项
        _default = next((t for b, t in choices if b == "hedge"), choices[0][1])
        self.backend_display = tk.StringVar(value=_default)
        self.backend_combo = ttk.Combobox(
            backend_bar, textvariable=self.backend_display, state="readonly",
            values=[text for _bid, text in choices], width=22)
        self.backend_combo.pack(side="left", padx=6)
        ttk.Label(backend_bar, text="（切换后下一轮生效）",
                  foreground="#777").pack(side="left")

        # 模式选择 + 按钮
        ctrl = ttk.Frame(self.root)
        ctrl.pack(padx=10, pady=4, before=backend_bar)   # 模式行/下一轮置最顶
        self.mode = tk.StringVar(value="normal")
        for val, text in MODE_ZH.items():
            ttk.Radiobutton(ctrl, text=text, variable=self.mode,
                            value=val).pack(side="left", padx=6)
        self.next_btn = ttk.Button(ctrl, text="下一轮", command=self.start_round, state="disabled")
        self.next_btn.pack(side="left", padx=12)

        # 按钮模式：手动出拳（初始隐藏，button 模式进入对局后才显示）
        self.move_bar = ttk.Frame(self.root)
        ttk.Label(self.move_bar, text="手动出拳：").pack(side="left")
        self.move_btns = {}
        for m in ("rock", "scissors", "paper"):
            b = ttk.Button(self.move_bar, text=ZH[m], state="disabled",
                           command=lambda mv=m: self.click_move(mv))
            b.pack(side="left", padx=6)
            self.move_btns[m] = b
        # 不 pack —— 进对局才显示

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def do_register(self):
        u, p = self.user_entry.get(), self.pwd_entry.get()
        try:
            self._auth.register(u, p)
        except self._auth.AuthError as e:
            self.status.set(f"注册失败：{e}")
        else:
            self.status.set(f"注册成功：{u}，请点登录")
            self.pwd_entry.delete(0, "end")

    def do_login(self):
        u, p = self.user_entry.get(), self.pwd_entry.get()
        try:
            self.user_id = self._auth.login(u, p)
        except self._auth.AuthError as e:
            self.status.set(f"登录失败：{e}")
        else:
            self.user_label.set(f"已登录：{self.user_id}")
            self.status.set(f"欢迎 {self.user_id}，选模式点「下一轮」")
            self.next_btn.config(state="normal")
            self.pwd_entry.delete(0, "end")

    def post(self, event):
        self.events.put(event)

    def click_move(self, move):
        """按钮模式：用户点拳作为承诺，发给 worker。"""
        for b in self.move_btns.values():
            b.config(state="disabled")
        self.human_var.set(ZH[move] + " ✓")
        self.commands.put(("human_move", move))

    def start_round(self):
        if self.user_id is None:
            self.status.set("请先登录")
            return
        self.next_btn.config(state="disabled")
        self.big.set("准备")
        self.result.set("")
        self.human_var.set(ZH[None])
        self.agent_var.set(ZH[None])
        mode = self.mode.get()
        if mode == "button":
            # ⚠️ 必须用 before= 固定位置：pack_forget 后重新 pack 会排到布局末尾。
            # 插在「裁判结果」之前，保证按钮行在有摄像头预览时也稳定在可视区内。
            self.move_bar.pack(padx=10, pady=6, before=self.result_label)
            for b in self.move_btns.values():
                b.config(state="normal")
            self.big.set("请出拳")
            self.status.set("点石头/剪刀/布出手；Agent 可同时通过 MCP 提交决策")
        else:
            self.move_bar.pack_forget()
        self.commands.put(("start", mode, self.user_id, self.current_backend()))

    def current_backend(self):
        """当前下拉选中的后端 id。"""
        return self.backend_label.get(self.backend_display.get(), "random")

    def _on_close(self):
        self.commands.put(("quit",))
        self.root.destroy()

    def _drain(self):
        try:
            while True:
                self._apply(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(40, self._drain)

    def _apply(self, ev):
        kind = ev[0]
        if kind == "countdown":
            v = ev[1]
            self.big.set(str(v) if isinstance(v, int) and v > 0 else
                         ("出拳！" if v == 0 else str(v)))
        elif kind == "status":
            self.status.set(ev[1])
        elif kind == "per_backend":
            self.per_backend.set(ev[1])
        elif kind == "taunt":
            # 机器人对人说的话（心理战后端）；空串=清空
            self.taunt.set(ev[1])
        elif kind == "too_fast":
            # 报障：人比模型快。在醒目位置提示（大字区 + 结果区配色）
            if ev[1]:
                self.big.set("太快了！模型没来得及决策")
                self.result.set("…等模型")
            else:
                self.result.set("")
        elif kind == "arm":
            self.big.set("✊" * ev[1] + ("  现在出手！" if ev[1] >= 2 else "  再来一下"))
        elif kind == "robot_ready":
            self.agent_var.set(ZH[ev[1]] + " ✓")   # 机器人已锁定（arm 模式提前亮锁定，不亮结果语义）
        elif kind == "committed":
            h, a = ev[1], ev[2]
            if h is not None:
                self.human_var.set(ZH[h] + " ✓")
            if a is not None:
                self.agent_var.set(ZH[a] + " ✓")
        elif kind == "reveal":
            h, a, result, score_str = ev[1], ev[2], ev[3], ev[4]
            bout, rnd, in_bout, per_bout = ev[5], ev[6], ev[7], ev[8]
            self.human_var.set(ZH[h])
            self.agent_var.set(ZH[a])
            self.result.set(RESULT_ZH[result])
            self.score.set(score_str)
            self.big.set(f"本轮结束 · 第{bout}局 {in_bout}/{per_bout}轮")
            self.move_bar.pack_forget()   # 收起手动出拳按钮
        elif kind == "busy":
            self.next_btn.config(state="disabled" if ev[1] else "normal")
        elif kind == "frame":
            img = ev[1]
            w, h = img.size
            # 窗口内宽约 660px，等比缩放
            target_w = 660
            if w != target_w:
                img = img.resize((target_w, int(h * target_w / w)))
            self._photo = ImageTk.PhotoImage(img)
            self.cam_label.config(image=self._photo, text="")

    def run(self):
        self.root.after(40, self._drain)
        self.root.mainloop()
