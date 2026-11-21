"""三层对局历史：轮(round) → 局(bout) → 用户画像(profile)，多用户隔离 + 全局画像。

设计：
  - 轮：一次出拳裁决的完整记录（带 user_id），最小真相源
  - 局：固定 ROUNDS_PER_BOUT 轮一组，**每个用户独立分局**
  - 画像：profile(user) 按用户过滤算；global_profile() 算全体

Agent 回传按需分层：轮给明细、局给边界、用户给聚合。不塞一坨大 JSON。
"""
import time
from collections import Counter

from judge import MOVES

ROUNDS_PER_BOUT = 5


def _profile_from(rounds, bouts):
    """从给定的轮+局算画像（内部纯函数）。"""
    if not rounds:
        return {"total_rounds": 0}
    n = len(rounds)
    freq = Counter(r["human"] for r in rounds)
    results = Counter(r["result"] for r in rounds)

    switches_after_loss = 0
    losses = 0
    for i in range(1, n):
        if rounds[i - 1]["result"] == "agent":
            losses += 1
            if rounds[i]["human"] != rounds[i - 1]["human"]:
                switches_after_loss += 1

    bout_openers = [b["rounds"][0]["human"] for b in bouts if b["rounds"]]
    return {
        "total_rounds": n,
        "total_bouts": len(bouts),
        "human_move_freq": {m: freq.get(m, 0) for m in MOVES},
        "human_win_rate": round(results.get("human", 0) / n, 3),
        "agent_win_rate": round(results.get("agent", 0) / n, 3),
        "draw_rate": round(results.get("draw", 0) / n, 3),
        "switch_after_loss_rate": (
            round(switches_after_loss / losses, 3) if losses else None
        ),
        "bout_opener_freq": dict(Counter(bout_openers)),
    }


class History:
    def __init__(self, rounds_per_bout=ROUNDS_PER_BOUT):
        self.rounds_per_bout = rounds_per_bout
        self.round_log = []      # 全部轮（跨用户），按发生顺序
        self.bouts = []          # 全部局，每局带 user_id
        self._current = {}       # user_id -> 当前未封的局

    # ---------- 写入 ----------
    def add_round(self, human, agent, result, mode="normal", user_id="anonymous"):
        """记一轮，归入该用户当前的局；满 ROUNDS_PER_BOUT 轮封局。"""
        if human not in MOVES or agent not in MOVES or result not in ("human", "agent", "draw"):
            raise ValueError(f"非法轮数据: human={human} agent={agent} result={result}")

        cur = self._current.get(user_id)
        if cur is None or cur["closed"]:
            cur = {
                "index": sum(1 for b in self.bouts if b["user_id"] == user_id) + 1,
                "user_id": user_id,
                "rounds": [],
                "closed": False,
                "winner": None,
            }
            self.bouts.append(cur)
            self._current[user_id] = cur

        rec = {
            "round": sum(1 for r in self.round_log if r["user_id"] == user_id) + 1,
            "bout": cur["index"],
            "user_id": user_id,
            "human": human,
            "agent": agent,
            "result": result,
            "mode": mode,
            "ts": time.time(),
        }
        self.round_log.append(rec)
        cur["rounds"].append(rec)

        if len(cur["rounds"]) >= self.rounds_per_bout:
            self._close(cur)
        return rec

    def _close(self, b):
        hw = sum(1 for r in b["rounds"] if r["result"] == "human")
        aw = sum(1 for r in b["rounds"] if r["result"] == "agent")
        b["human_wins"] = hw
        b["agent_wins"] = aw
        b["draws"] = sum(1 for r in b["rounds"] if r["result"] == "draw")
        b["winner"] = "human" if hw > aw else ("agent" if aw > hw else "draw")
        b["closed"] = True
        return b

    # ---------- 分层回传 ----------
    def get_rounds(self, n=None, bout=None, user_id=None):
        data = self.round_log
        if user_id is not None:
            data = [r for r in data if r["user_id"] == user_id]
        if bout is not None:
            data = [r for r in data if r["bout"] == bout and
                    (user_id is None or r["user_id"] == user_id)]
        if n is not None:
            data = data[-n:]
        return [dict(r) for r in data]

    def get_bouts(self, user_id=None):
        out = []
        for b in self.bouts:
            if not b["closed"]:
                continue
            if user_id is not None and b["user_id"] != user_id:
                continue
            out.append({
                "bout": b["index"], "user_id": b["user_id"],
                "rounds": len(b["rounds"]),
                "human_wins": b["human_wins"], "agent_wins": b["agent_wins"],
                "draws": b["draws"], "winner": b["winner"],
                "first_round": b["rounds"][0]["round"],
                "last_round": b["rounds"][-1]["round"],
            })
        return out

    def profile(self, user_id=None):
        """user_id 给定=该用户画像；不给=全体全局画像。"""
        rounds = self.round_log if user_id is None else [r for r in self.round_log if r["user_id"] == user_id]
        bouts = [b for b in self.bouts if b["closed"]]
        if user_id is not None:
            bouts = [b for b in bouts if b["user_id"] == user_id]
        p = _profile_from(rounds, bouts)
        if user_id is not None:
            p["user_id"] = user_id
        return p

    def global_profile(self):
        return self.profile(user_id=None)

    def snapshot(self, user_id, recent_rounds=10):
        """Agent 一次调用拿三层：该用户轮明细 + 该用户局 + 该用户画像 + 全局画像。"""
        return {
            "user_id": user_id,
            "recent_rounds": self.get_rounds(n=recent_rounds, user_id=user_id),
            "bouts": self.get_bouts(user_id=user_id),
            "profile": self.profile(user_id=user_id),
            "global_profile": self.global_profile(),
        }

    def __str__(self):
        p = self.global_profile()
        if p["total_rounds"] == 0:
            return "无对局记录"
        return (f"共 {p['total_rounds']} 轮 / {p['total_bouts']} 局，"
                f"各拳 {p['human_move_freq']}，胜率 {p['human_win_rate']}")
