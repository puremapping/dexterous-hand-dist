"""出拳决策层——可插拔后端 + 统一协议 + 统一降级语义。

## 后端协议

每个后端是一个函数：f(context: dict) -> str | None
  context = {"history": [人历次出拳], "mode": "normal|arm|button"}
  返回   = "rock" / "paper" / "scissors"，或 None 表示「本轮我弃权」

**弃权（None）与崩溃的区别**：
  - 返回 None  → 后端主动弃权，走降级链；全链弃权则本轮跳过
  - 抛异常     → 后端故障，decide() 捕获 → 走降级链

降级链由 FALLBACKS 声明，后端自己不用写 try/except。

## 加新后端

    1. def my_move(context): ...                       # 符合上面的签名
    2. FALLBACKS["my"] = ["stats"]                     # 失败时退到哪（可选）
    3. BACKENDS["my"] = ("显示名", my_move)             # 注册（GUI 下拉自动出现）

调用方（duel.py / round_flow.py）不需要任何改动。
"""
import os
import random
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_COMMIT_FILE = os.path.join(HERE, "agent_commit.txt")
MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
# 克制 x 的拳（BEATS 的逆）——出拳要用这个，别用 BEATS
COUNTERS = {v: k for k, v in BEATS.items()}


# ─────────────────────────── 后端实现 ───────────────────────────

def random_move(context=None):
    """纯随机：完全不看对手。"""
    return random.choice(MOVES)


def stats_move(context=None):
    """纯统计：出对手主频的克制拳。无历史则随机。"""
    hist = (context or {}).get("history") or []
    if not hist:
        return random.choice(MOVES)
    freq = Counter(hist[-10:])
    top = max(freq.items(), key=lambda kv: kv[1])[0]
    return COUNTERS[top]


def jev_move(context=None, min_confidence=0.55):
    """Jev 决策模型：预测对手下一手 → 出克它的拳。置信度低则弃权（交给降级链）。"""
    import jev_client
    ctx = context or {}
    hist = ctx.get("history") or []
    move, conf, raw = jev_client.decide_move(
        hist, mode_hint=ctx.get("mode", ""), min_confidence=min_confidence)
    if move is None:
        print(f"[jev] confidence={conf} 过低，弃权", flush=True)
        return None
    print(f"[jev] 出 {move}（confidence={conf} 概率={raw['probabilities']}）", flush=True)
    return move


def _normalize(counter, keys=MOVES):
    total = sum(counter.get(k, 0) for k in keys)
    if total <= 0:
        return None
    return {k: counter.get(k, 0) / total for k in keys}


def pred_freq(hist, my_moves=None):
    """全历史频率（取最近 20 手）：对手整体偏好。"""
    if not hist:
        return None
    return _normalize(Counter(hist[-20:]))


def pred_recent(hist, my_moves=None):
    """最近 5 手频率：捕捉对手当前态势的变化。"""
    if len(hist) < 3:
        return None
    return _normalize(Counter(hist[-5:]))


def pred_transition(hist, my_moves=None):
    """一阶转移：P(下一手 | 对手上一手)。"""
    if len(hist) < 4:
        return None
    last = hist[-1]
    counter = Counter()
    for i in range(len(hist) - 1):
        if hist[i] == last:
            counter[hist[i + 1]] += 1
    return _normalize(counter)


def pred_repeat(hist, my_moves=None):
    """对手模仿型：下一手 = 我上一手。"""
    if not my_moves:
        return None
    return {m: (1.0 if m == my_moves[-1] else 0.0) for m in MOVES}


def pred_counter_me(hist, my_moves=None):
    """对手反制型：下一手 = 克制我上一手的拳。"""
    if not my_moves:
        return None
    target = COUNTERS[my_moves[-1]]
    return {m: (1.0 if m == target else 0.0) for m in MOVES}


def pred_jev(hist, my_moves=None):
    """Jev 模型预测的对手分布。失败/熔断时返回 None（融合静默跳过这一路）。"""
    import jev_client
    try:
        _move, _conf, raw = jev_client.decide_move(hist, min_confidence=0.0)
    except Exception:
        return None
    probs = (raw or {}).get("probabilities")
    if not probs:
        return None
    return {m: float(probs.get(m, 0)) for m in MOVES}


# 融合里是否启用 Jev 那一路。实测（50-80 轮）不启用 77.1% vs 启用 76.8%
# ——统计预测器已覆盖它能提供的信息，却要付 570ms。默认关，需要语义判断时再开。
USE_JEV_IN_FUSION = os.environ.get("FUSION_JEV", "0") == "1"

# 预测器注册：名字 -> (函数, 先验权重)。先验是「上线时给多少」，之后由准确率接管。
PREDICTORS = {
    "freq":       (pred_freq, 0.10),
    "recent":     (pred_recent, 0.25),
    "transition": (pred_transition, 0.25),
    "repeat":     (pred_repeat, 0.20),
    "counter_me": (pred_counter_me, 0.20),
    "jev":        (pred_jev, 0.20),
}


class _AccuracyTracker:
    """在线追踪各预测器的命中率（EMA），权重随准确率走。

    每轮拿「上一轮的预测」和「本轮实际对手出拳」比，对的加分、错的减分。
    EMA 让权重缓慢移动，不会一轮抽风就翻盘。
    """

    def __init__(self, decay=0.85):
        self.decay = decay
        self.ema = {}          # name -> 命中率 EMA
        self.pending = None    # 上一轮各预测器给出的分布

    def update(self, hist, new_preds):
        if self.pending is not None and hist:
            actual = hist[-1]
            for name, p in self.pending.items():
                hit = 1.0 if max(p, key=p.get) == actual else 0.0
                prev = self.ema.get(name, 0.34)     # 未开张时按 1/3 起步
                self.ema[name] = self.decay * prev + (1 - self.decay) * hit
        self.pending = new_preds

    def weight(self, name, prior):
        """准确率^3 × 先验：差距被放大，最准的一路迅速主导，不中的快速沉底。"""
        e = max(self.ema.get(name, prior), 0.02)
        return (e ** 3) * prior


def new_tracker():
    """给每个玩家建一个独立的准确率追踪器（机机对决时各用各的）。"""
    return _AccuracyTracker()


# 默认 tracker 仅为兼容旧调用（测试脚本等）保留；正常对局一律从 context 传
_TRACKER = new_tracker()


def _ev_given_opp_probs(p):
    """给定「对手出各手势的概率」，算我出各手势的期望收益（赢+1/平0/输-1）。"""
    return {
        "rock":     p["scissors"] - p["paper"],
        "paper":    p["rock"] - p["scissors"],
        "scissors": p["paper"] - p["rock"],
    }


def _softmax_pick(ev, beta):
    """按期望收益的 softmax 分布采样：分高概率大，但不必然——防止被看穿。"""
    import math
    mx = max(ev.values())
    weights = {m: math.exp(beta * (v - mx)) for m, v in ev.items()}
    total = sum(weights.values())
    r = random.random() * total
    acc = 0.0
    for m, w in weights.items():
        acc += w
        if r <= acc:
            return m
    return max(weights, key=weights.get)


def fusion_move(context=None, beta=1.5, verbose=True):
    """多预测器融合 + softmax 采样。

    多路预测器各自给出「对手下一手」的分布 → 加权平均 → 换算成期望收益
    → softmax 采样出拳。单路被带偏时其他路能拉住；采样保证出拳模式不固定。
    """
    ctx = context or {}
    hist = ctx.get("history") or []
    my_moves = ctx.get("my_moves") or []
    tracker = ctx.get("tracker") or _TRACKER   # 每个玩家一个；缺省回落全局（兼容）

    preds = {}
    for name, (fn, _w) in PREDICTORS.items():
        if name == "jev" and not USE_JEV_IN_FUSION:
            continue
        try:
            p = fn(hist, my_moves)
        except Exception as e:
            if verbose:
                print(f"[fusion] 预测器 {name} 失败: {type(e).__name__} {e}", flush=True)
            continue
        if p:
            preds[name] = p

    if not preds:
        return random.choice(MOVES)

    # 先更新准确率（用上轮预测 vs 本轮实际），再按准确率定权重
    tracker.update(hist, preds)

    fused = {m: 0.0 for m in MOVES}
    wtotal = 0.0
    used_w = {}
    for name, p in preds.items():
        w = tracker.weight(name, PREDICTORS[name][1])
        used_w[name] = w
        wtotal += w
        for m in MOVES:
            fused[m] += w * p.get(m, 0.0)
    if wtotal <= 0:
        return random.choice(MOVES)
    fused = {m: v / wtotal for m, v in fused.items()}

    ev = _ev_given_opp_probs(fused)
    move = _softmax_pick(ev, beta)

    if verbose:
        f = " ".join(f"{m[:2]}:{v:.2f}" for m, v in fused.items())
        e = " ".join(f"{m[:2]}:{v:+.2f}" for m, v in ev.items())
        w = " ".join(f"{k}:{v:.2f}" for k, v in used_w.items())
        print(f"[fusion] 权重[{w}] 对手预测[{f}] EV[{e}] → 出 {move}",
              flush=True)
    return move


def external(context=None, timeout=20.0, poll=0.1):
    """外部 Agent 注入：等到承诺文件出现，读后一次性消费。等不到则弃权。

    超时设 20s：决策线程与出拳并行，正常情况人的承诺先到、或 Agent 已在
    窗口期提交；20s 是「两边都没动静」时的兜底上限，避免揭晓无限等。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(AGENT_COMMIT_FILE):
            with open(AGENT_COMMIT_FILE, encoding="utf-8") as f:
                move = f.read().strip().lower()
            os.remove(AGENT_COMMIT_FILE)   # 一次性消费
            if move not in MOVES:
                raise ValueError(f"agent 提交的手势非法: {move}")
            return move
        time.sleep(poll)
    print(f"[external] {timeout:.0f}s 内没收到 Agent 承诺，弃权", flush=True)
    return None


# ─────────────────────────── 注册表 ───────────────────────────

# 后端 id -> (GUI 显示名, 函数)
BACKENDS = {
    "random":   ("随机", random_move),
    "stats":    ("统计（克主频）", stats_move),
    "jev":      ("Jev（决策模型）", jev_move),
    "fusion":   ("融合（多预测器+采样）", fusion_move),
    "external": ("外部 Agent（MCP）", external),
}

# 后端 id -> 失败时的降级顺序（不含自己）
FALLBACKS = {
    "fusion": ["stats", "random"],
    "jev": ["stats", "random"],
    "external": ["stats", "random"],
    "stats": ["random"],
}


# ─────────────────────────── 统一入口 ───────────────────────────

def decide(strategy="random", context=None):
    """执行后端，异常与弃权统一走降级链。返回 move 或 None。"""
    chain = [strategy] + list(FALLBACKS.get(strategy, []))
    for name in chain:
        fn = BACKENDS.get(name, (None, None))[1]
        if fn is None:
            continue
        try:
            move = fn(context)
        except Exception as e:
            print(f"[decide] 后端 {name} 失败: {type(e).__name__} {e}", flush=True)
            continue
        if move in MOVES:
            if name != strategy:
                print(f"[decide] 降级到 {name} → {move}", flush=True)
            return move
    return None


def validate(strategy):
    if strategy not in BACKENDS:
        raise ValueError(f"未知后端 {strategy}，可选: {', '.join(BACKENDS)}")


def backend_choices():
    """GUI 下拉用：[(id, 显示名), ...]"""
    return [(k, v[0]) for k, v in BACKENDS.items()]
