"""固定话术-出拳配对后端（阿哲 2026-11-19 定）。

设计纪律：
  - 不做预测、不做学习。M（出拳）由 (话里的手势 G, 方向) 用规则直接算出。
  - 每格 = (话术id 1..18, 方向 comply|counter)，共 36 格，每格独立记 胜/负/平。
  - 「对手会出 G 还是克制 G」是写死的二选一假设，不是学到的预测器。
  - 方向必须在出拳前锁定，不能事后按对手实际出拳选——那是偷看。

36 格的胜负表落盘 fixed_pairs_stats.json，供之后分析；不进决策回路。

统计口径：result 是机器人视角 +1 胜 / 0 平 / -1 负。
"""
import json
import math
import os
import random
import threading
from collections import Counter

import arena as A
from strategy import MOVES, COUNTERS, BEATS

HERE = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(HERE, "fixed_pairs_stats.json")
DIAG_FILE = os.path.join(HERE, "diag_stats.json")   # 诊断实验独立落盘，只存 D1/D2 命中率

# 18 句：每句含一个目标手势 G。措辞三类（断言/激将/赌），指向三手势。
# (id, 话术模板, G)
UTTERANCES = [
    ("P01", "我看穿了，你这把出石头。", "rock"),
    ("P02", "我看穿了，你这把出布。", "paper"),
    ("P03", "我看穿了，你这把出剪刀。", "scissors"),
    ("P04", "你不敢出石头吧？", "rock"),
    ("P05", "你不敢出布吧？", "paper"),
    ("P06", "你不敢出剪刀吧？", "scissors"),
    ("P07", "别磨蹭，出石头吧。", "rock"),
    ("P08", "别磨蹭，出布吧。", "paper"),
    ("P09", "别磨蹭，出剪刀吧。", "scissors"),
    ("P10", "快点，敢不敢出石头？", "rock"),
    ("P11", "快点，敢不敢出布？", "paper"),
    ("P12", "快点，敢不敢出剪刀？", "scissors"),
    ("P13", "我赌你出石头。", "rock"),
    ("P14", "我赌你出布。", "paper"),
    ("P15", "我赌你出剪刀。", "scissors"),
    ("P16", "我赌你不敢出石头。", "rock"),
    ("P17", "我赌你不敢出布。", "paper"),
    ("P18", "我赌你不敢出剪刀。", "scissors"),
]

DIRECTIONS = ("comply", "counter")   # comply=对手出G ; counter=对手出克制G 的拳
U_BY_ID = {uid: (text, g) for uid, text, g in UTTERANCES}


def expected_opp(g, direction):
    """假设对手会出的拳。

    comply  : 对手顺着话出 G
    counter : 对手看穿「我要出克制G的拳」，于是出能克制那一步的拳
              （= counter_own 再推一层：COUNTERS[COUNTERS[g]] = BEATS[g]）
    """
    if direction == "comply":
        return g
    # 我看穿后出 COUNTERS[g]；对手再克它 → COUNTERS[COUNTERS[g]]，即 BEATS[g]
    return BEATS[g]


def counter_move(g, direction):
    """我方出拳 = 克制「预期对手拳」。

    comply  : 克制 G
    counter : 克制 BEATS[g]（比对手多看一层）
    """
    return COUNTERS[expected_opp(g, direction)]


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def _key(uid, direction):
    return f"{uid}:{direction}"


class AdaptiveDirectionPlayer(A.Player):
    """在线调权：每轮学「这人对带指向的话，是顺从还是逆反」。

    跟 Hedge 同构——两层：
      内层：话术随机（均匀采样，不学）
      外层：方向权重 comply/counter，每轮按「押对了吗」在线更新

    押对 = 对手实际出拳 == 该方向预期的对手拳。乘法权重（exp(-η·loss)），
    猜中该方向吃小亏、猜错吃大亏，权重自乘后归一化。η 越大切换越快。

    与 SampledPairPlayer 的区别：那个只记账不学习；这个每轮都在改方向权重。
    """

    def __init__(self, seed=None, eta=0.55, only=None):
        self._raw_rng = random.Random(seed)
        self.eta = eta
        self.only = only
        self._lock = threading.Lock()
        self._draw_key = None
        self._draw = None
        self.weights = {"comply": 1.0, "counter": 1.0}   # 方向权重，跨轮累计
        self.history = []                                  # (direction, 押中?) 供观察

    @property
    def name(self):
        # 中性代号：权重/方向只在文件里，不显示（权重就是明牌，会污染被试）
        return "后端 A1"

    def _pick_direction(self):
        """按权重采样方向（权重高更可能选）。"""
        w = self.weights
        tot = w["comply"] + w["counter"]
        r = self._raw_rng.random() * tot
        return "comply" if r <= w["comply"] else "counter"

    def _sample_for(self, round_key):
        with self._lock:
            if self._draw_key == round_key and self._draw is not None:
                return self._draw
            pool = self.only or [uid for uid, _t, _g in UTTERANCES]
            uid = pool[int(self._raw_rng.random() * len(pool)) % len(pool)]
            direction = self._pick_direction()          # 按权重选，不是均匀
            text, g = U_BY_ID[uid]
            move = counter_move(g, direction)
            self._draw_key = round_key
            self._draw = (uid, direction, text, g, move)
            return self._draw

    @staticmethod
    def _round_key(view):
        return len(view.get("opp", []))

    def decide(self, view):
        return self._sample_for(self._round_key(view))[4]

    def talk(self, view):
        return self._sample_for(self._round_key(view))[2]

    def observe(self, my_move, opp_move, result):
        with self._lock:
            if self._draw is None:
                return
            uid, direction, text, g, move = self._draw
            # 该方向的预测是否命中：对手实际出的拳 == 我预期的对手拳？
            hit = 1.0 if opp_move == expected_opp(g, direction) else 0.0
            # 乘法权重：命中 loss=0 权重不变；未命中 loss=1 权重乘 exp(-η)
            loss = 1.0 - hit
            self.weights[direction] *= math.exp(-self.eta * loss)
            # 防权重过小
            total = self.weights["comply"] + self.weights["counter"]
            if total < 1e-9:
                self.weights = {"comply": 1.0, "counter": 1.0}
            self.history.append((direction, hit))
            # 记账（保留明细供查看）
            stats = load_stats()
            k = _key(uid, direction)
            row = stats.setdefault(k, {"utterance": text, "g": g, "direction": direction,
                                       "move": move, "win": 0, "lose": 0, "draw": 0,
                                       "hit": 0, "miss": 0, "opp_actual": {}})
            row.setdefault("opp_actual", {})
            row.setdefault("hit", 0); row.setdefault("miss", 0)
            row["opp_actual"][opp_move] = row["opp_actual"].get(opp_move, 0) + 1
            row["hit" if hit else "miss"] += 1
            if result == 1:
                row["win"] += 1
            elif result == -1:
                row["lose"] += 1
            else:
                row["draw"] += 1
            save_stats(stats)

    def clone(self):
        import copy
        return copy.deepcopy(self)


def all_pairs():
    """列出 36 格：(id, direction, 话术, G, 预期对手, 我方出拳)。"""
    out = []
    for uid, text, g in UTTERANCES:
        for d in DIRECTIONS:
            out.append((uid, d, text, g, expected_opp(g, d), counter_move(g, d)))
    return out


def stats_summary():
    """按胜率排序输出每格战绩（胜/(胜+负)）。"""
    stats = load_stats()
    rows = []
    for k, r in stats.items():
        w, l, d = r["win"], r["lose"], r["draw"]
        rate = w / (w + l) if (w + l) else 0.0
        rows.append((rate, k, w, l, d))
    rows.sort(reverse=True)
    return rows