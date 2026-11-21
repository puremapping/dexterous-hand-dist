"""竞技场：统一玩家适配器 + 人类 RPS 模拟器 + 基准测试。

设计目标（阿哲 2026-11-19 定的）：
  1. 机器人决策后端和「人类模拟对手」都实现同一个 Player 接口——机机对决时
     两边都是 Player，无人/有人只是 view 来源不同。
  2. 加新后端不用改测试/对局循环：无状态后端自动走 strategy.decide，
     有状态后端才需要写一个 decide/observe 类。
  3. benchmark 用统一 match()，不再有 `if backend == ... elif ...`。

Player 协议
    decide(view) -> move       view = {"opp": 对手历次出拳, "me": 我历次出拳}
    observe(my, opp, result)   揭晓后更新内部状态；result 是我方视角 +1/0/-1
    clone()                    造状态独立的新副本（并行/机机对决用）

人类模型来自认知科学实测文献：
    WSLS     赢则保持、输则换拳（人类最强规律）
    0-ToM    Dirichlet 追踪 + 指数遗忘 + softmax（Fusaroli 认知建模课件）
    CR       条件响应 6 参数 Γ（Wang et al. PMC5376050，零拟合复现群体循环）
    Brockbank 7 bots  Brockbank & Vul 2024，人类对它们的胜率是实测基准
"""
import copy
import math
import random
from collections import Counter

import strategy as S

MOVES = S.MOVES                      # rock, paper, scissors
BEATS = S.BEATS                      # BEATS[x] = x 克制的拳
COUNTERS = S.COUNTERS                # COUNTERS[x] = 克制 x 的拳


def outcome(a, b):
    """a 对 b 的结果：+1 a 赢 / 0 平 / -1 a 输。"""
    if a == b:
        return 0
    return 1 if BEATS[a] == b else -1


def _shift(move, k):
    """在 R→P→S→R 循环里平移 k 步（k∈{-1,0,1}）。"""
    return MOVES[(MOVES.index(move) + k) % 3]


def _sample(probs, rng):
    r = rng() * sum(probs)
    acc = 0.0
    for k, p in enumerate(probs):
        acc += p
        if r <= acc:
            return k
    return len(probs) - 1


def _softmax_pick(ev, beta, rng):
    mx = max(ev.values())
    w = [math.exp(beta * (ev[m] - mx)) for m in MOVES]
    return MOVES[_sample(w, rng)]


class Player:
    """统一玩家接口。机器人后端和人类模拟器都继承它。"""

    def decide(self, view):
        raise NotImplementedError

    def observe(self, my_move, opp_move, result):
        pass                      # 无状态玩家默认 no-op

    def clone(self):
        return copy.deepcopy(self)

    @property
    def name(self):
        return self.__class__.__name__


# ───────────────────── 机器人后端适配 ─────────────────────

class RobotPlayer(Player):
    """把 strategy 里的后端包装成 Player。

    无状态后端（random/stats/jev/external）自动走 S.decide，零适配。
    有状态的 fusion 用一个独立 tracker（存在实例内，不再塞进 ctx 传来传去）。
    """

    def __init__(self, backend="fusion", beta=1.5, verbose=False, **kw):
        S.validate(backend)
        self.backend = backend
        self.beta = beta
        self.verbose = verbose
        self.kw = kw
        self._tracker = S.new_tracker() if backend == "fusion" else None

    @property
    def name(self):
        display = S.BACKENDS[self.backend][0]
        return f"机器人/{display}"

    def decide(self, view):
        ctx = {
            "history": list(view.get("opp", [])),
            "my_moves": list(view.get("me", [])),
            "mode": "",
        }
        if self._tracker is not None:
            ctx["tracker"] = self._tracker
        if self.backend == "fusion":
            return S.fusion_move(ctx, beta=self.beta, verbose=self.verbose)
        return S.decide(self.backend, ctx)


# ───────────────────── 人类行为模拟 ─────────────────────

class RandomHuman(Player):
    """真随机——RPS 的理论最优基线。任何'胜过它'的模型都是自欺。"""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    @property
    def name(self):
        return "人类/随机"

    def decide(self, view):
        return self.rng.choice(MOVES)


class WSLSAgent(Player):
    """Win-Stay / Lose-Shift：人类最强的经验规律。

    按上一轮自己的结果决定平移方向。参数是可解释的三组概率：
      win  -> [stay, CW, CCW]，lose -> [...]，draw -> [...]
    """

    def __init__(self, seed=None,
                 theta_win=(0.80, 0.10, 0.10),
                 theta_lose=(0.10, 0.70, 0.20),
                 theta_draw=(0.40, 0.30, 0.30)):
        self.rng = random.Random(seed)
        self.theta = {1: theta_win, -1: theta_lose, 0: theta_draw}
        self.last_move = None
        self.last_result = None

    @property
    def name(self):
        return "人类/WSLS"

    def decide(self, view):
        if self.last_move is None:
            return self.rng.choice(MOVES)
        k = _sample(self.theta[self.last_result], self.rng.random) - 1  # 0->-1,1->0,2->+1
        return _shift(self.last_move, k)

    def observe(self, my_move, opp_move, result):
        self.last_move = my_move
        self.last_result = result


class ZeroToMAgent(Player):
    """0-ToM：Dirichlet 信念追踪 + 指数遗忘 + softmax 决策。

    alpha 是对「对手出拳分布」的信念；每轮先遗忘回均匀，再计入观察。
    log_sigma 控制遗忘率，log_beta 控制决策温度（都用 log 保证正）。
    """

    def __init__(self, seed=None, log_sigma=0.0, log_beta=0.0):
        self._raw_rng = random.Random(seed)
        self.rng = self._raw_rng.random   # 统一成 callable，供 _sample 用
        self.rho = math.exp(-math.exp(log_sigma))   # sigma=0 -> rho=1 不遗忘
        self.beta = math.exp(log_beta)
        self.alpha = [1.0, 1.0, 1.0]

    @property
    def name(self):
        return "人类/0-ToM"

    def decide(self, view):
        opp = view.get("opp", [])
        if opp:                       # 遗忘只在有观察后进行
            self.alpha = [self.rho * a + (1 - self.rho) for a in self.alpha]
        total = sum(self.alpha)
        p_opp = {m: self.alpha[i] / total for i, m in enumerate(MOVES)}
        ev = {m: sum(p_opp[u] * outcome(m, u) for u in MOVES) for m in MOVES}
        return _softmax_pick(ev, self.beta, self.rng)

    def observe(self, my_move, opp_move, result):
        self.alpha[MOVES.index(opp_move)] += 1


class ConditionalResponseAgent(Player):
    """CR 模型（Wang et al.）：按上一轮结果 W/T/L 决定转向。

    gamma[result] = [P(stay), P(CW), P(CCW)]。Wang 的实证参数大致是
    赢偏 stay、输偏 CW（social cycling 的来源），平局近均匀。
    """

    def __init__(self, seed=None,
                 gamma_w=(0.55, 0.25, 0.20),
                 gamma_t=(0.35, 0.35, 0.30),
                 gamma_l=(0.20, 0.55, 0.25)):
        self.rng = random.Random(seed)
        self.gamma = {1: gamma_w, 0: gamma_t, -1: gamma_l}
        self.last_move = None
        self.last_result = None

    @property
    def name(self):
        return "人类/CR"

    def decide(self, view):
        if self.last_move is None:
            return self.rng.choice(MOVES)
        k = _sample(self.gamma[self.last_result], self.rng.random) - 1
        return _shift(self.last_move, k)

    def observe(self, my_move, opp_move, result):
        self.last_move = my_move
        self.last_result = result


class BrockbankBot(Player):
    """Brockbank & Vul 2024 的 algorithmic opponents（7 种）+ 10% 噪声。

    kind:
      self+ / self-   出【克制 / 输给】自己上一手的拳
      opp+  / opp0    出【克制 / 重复】对手上一手的拳
      outcome1        赢→stay，输→self+，平→self-
      outcome2        赢→self+，输→self-，平→stay
      outcome_trans   按（上轮结果 × 上轮自转移）组合（最复杂，人类打不过）
    """

    KINDS = ("self+", "self-", "opp+", "opp0",
             "outcome1", "outcome2", "outcome_trans")

    def __init__(self, kind="self+", seed=None, noise=0.10):
        if kind not in self.KINDS:
            raise ValueError(f"未知 bot 类型 {kind}，可选 {self.KINDS}")
        self.kind = kind
        self.rng = random.Random(seed)
        self.noise = noise
        self.my_last = None
        self.opp_last = None
        self.last_result = None
        self.last_self_shift = None

    @property
    def name(self):
        return f"人类/Brockbank-{self.kind}"

    def _rule(self):
        k = self.kind
        if k == "self+":
            return COUNTERS[self.my_last] if self.my_last else None
        if k == "self-":
            return BEATS[self.my_last] if self.my_last else None
        if k == "opp+":
            return COUNTERS[self.opp_last] if self.opp_last else None
        if k == "opp0":
            return self.opp_last
        if k == "outcome1":
            if self.my_last is None:
                return None
            if self.last_result == 1:
                return self.my_last
            if self.last_result == -1:
                return COUNTERS[self.my_last]
            return BEATS[self.my_last]
        if k == "outcome2":
            if self.my_last is None:
                return None
            if self.last_result == 1:
                return COUNTERS[self.my_last]
            if self.last_result == -1:
                return BEATS[self.my_last]
            return self.my_last
        # outcome_trans：结果 × 上轮自转移的组合规则
        if self.my_last is None:
            return None
        if self.last_result == 1:
            return _shift(self.my_last, 0 if self.last_self_shift in (0, 1) else 1)
        if self.last_result == -1:
            return _shift(self.my_last, 1 if self.last_self_shift in (0, 1) else -1)
        return _shift(self.my_last, -1)

    def decide(self, view):
        if self.rng.random() < self.noise:
            return self.rng.choice(MOVES)
        target = self._rule()
        return target if target in MOVES else self.rng.choice(MOVES)

    def observe(self, my_move, opp_move, result):
        if self.my_last is not None:
            self.last_self_shift = (MOVES.index(my_move) - MOVES.index(self.my_last)) % 3
        self.my_last = my_move
        self.opp_last = opp_move
        self.last_result = result


class Copycat(Player):
    """简单基线：模仿对手上一手。"""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    @property
    def name(self):
        return "人类/模仿"

    def decide(self, view):
        opp = view.get("opp", [])
        return opp[-1] if opp else self.rng.choice(MOVES)


class CounterPlayer(Player):
    """简单基线：出克制对手上一手的拳。"""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    @property
    def name(self):
        return "人类/反制上一手"

    def decide(self, view):
        opp = view.get("opp", [])
        return COUNTERS[opp[-1]] if opp else self.rng.choice(MOVES)


# ───────────────────── 人类对手注册表 ─────────────────────

def make_human(kind, seed=None):
    """按名字造一个人类模拟器。统一入口，benchmark 不再 if-else。"""
    factories = {
        "random":   lambda: RandomHuman(seed),
        "random_alt":  lambda: RandomHuman(seed),
        "wsls":     lambda: WSLSAgent(seed),
        "ztom":     lambda: ZeroToMAgent(seed),
        "cr":       lambda: ConditionalResponseAgent(seed),
        "copycat":  lambda: Copycat(seed),
        "counter":  lambda: CounterPlayer(seed),
    }
    if kind in BrockbankBot.KINDS:
        return BrockbankBot(kind, seed)
    if kind not in factories:
        raise ValueError(f"未知人类模型 {kind}，可选 {list(factories) + list(BrockbankBot.KINDS)}")
    return factories[kind]()


HUMAN_KINDS = ["random", "wsls", "ztom", "cr", "copycat", "counter"] + list(BrockbankBot.KINDS)


# ───────────────────── 统一对局 ─────────────────────

def match(robot, human, rounds=200, seed=None):
    """机器人 vs 人类模拟器，跑 rounds 轮。返回机器人视角战绩。

    关键：两边 view 对称——谁的 view，opp 就是谁面对的对手。
    所以人类模型看的是机器人历史，机器人看的是人类历史。
    """
    rng = random.Random(seed)
    robot = robot.clone()
    human = human.clone()
    rv = {"opp": [], "me": []}      # 机器人视角
    hv = {"opp": [], "me": []}      # 人类视角
    w = d = l = 0
    for _ in range(rounds):
        rm = robot.decide(rv)
        hm = human.decide(hv)
        if rm not in MOVES or hm not in MOVES:
            continue
        r = outcome(rm, hm)          # +1 机器人赢 / 0 平 / -1 机器人输
        if r == 1:
            w += 1
        elif r == -1:
            l += 1
        else:
            d += 1
        robot.observe(rm, hm, r)
        human.observe(hm, rm, -r)
        rv["opp"].append(hm); rv["me"].append(rm)
        hv["opp"].append(rm); hv["me"].append(hm)
    return {"wins": w, "draws": d, "losses": l,
            "win_rate": round(w / (w + l), 3) if (w + l) else 0.0}


# ───────────────────── Hedge 集成（翻自 mind-reader-rps，MIT） ─────────────────────

class HedgePlayer(Player):
    """7 专家 + Hedge 加权融合（源自 Devapriyan-S/mind-reader-rps, MIT）。

    与源实现的两处关键差异（见 reference 档案）：
      1. 出拳用 softmax 采样，**不用它的纯 argmax**——确定性可被看穿。
      2. 结果方向统一到本项目：内部 history 的 result 是「人类视角」。

    专家：uniform / favourite / markov-1/2/3 / win-stay / second-guess。
    融合：loss=-log(p[实际])，weight *= exp(-eta*loss)，再归一化（Hedge，
    regret O(sqrt(T log N))）。Laplace ALPHA=0.6 防冷启动断言。
    """

    ALPHA = 0.6

    def __init__(self, eta=0.55, beta=1.5, seed=None, mode="softmax"):
        self.eta = eta
        self.beta = beta
        self.mode = mode                      # softmax | argmax（argmax 仅做消融对照）
        self._raw_rng = random.Random(seed)
        self.rng = self._raw_rng.random
        self.hist = []                        # [{player, ai, result}] result=人类视角
        self.weights = [1.0] * 7
        self.experts = [
            self._e_uniform(), self._e_favourite(),
            self._e_markov(1), self._e_markov(2), self._e_markov(3),
            self._e_outcome(), self._e_second_guess(),
        ]

    @property
    def name(self):
        return f"机器人/Hedge7-{self.mode}"

    # ---- 专家 ----
    def _norm(self, counts):
        s = sum(counts)
        return [c / s for c in counts] if s > 0 else [1 / 3] * 3

    def _from_counts(self, counts):
        return self._norm([c + self.ALPHA for c in counts])

    def _e_uniform(self):
        return {"predict": lambda h: [1 / 3] * 3, "update": lambda h: None}

    def _e_favourite(self):
        counts = [0, 0, 0]
        return {
            "predict": lambda h: self._from_counts(counts),
            "update": lambda h: counts.__setitem__(MOVES.index(h[-1]["player"]),
                                                   counts[MOVES.index(h[-1]["player"])] + 1),
        }

    def _e_markov(self, k):
        table = {}
        key = lambda h: ">".join(r["player"] for r in h[-k:])
        def predict(h):
            if len(h) < k:
                return [1 / 3] * 3
            return self._from_counts(table.get(key(h), [0, 0, 0]))
        def update(h):
            if len(h) < k + 1:
                return
            ctx = ">".join(r["player"] for r in h[-(k + 1):-1])
            row = table.setdefault(ctx, [0, 0, 0])
            row[MOVES.index(h[-1]["player"])] += 1
        return {"predict": predict, "update": update}

    def _e_outcome(self):
        """win-stay：按「上一轮的(结果,人类出拳)」预测这一轮人类出拳。"""
        table = {}
        key = lambda r: f"{r['result']}:{r['player']}"
        def predict(h):
            if not h:
                return [1 / 3] * 3
            return self._from_counts(table.get(key(h[-1]), [0, 0, 0]))
        def update(h):
            if len(h) < 2:
                return
            row = table.setdefault(key(h[-2]), [0, 0, 0])
            row[MOVES.index(h[-1]["player"])] += 1
        return {"predict": predict, "update": update}

    def _e_second_guess(self):
        """对手在想「它会预期我出什么」：repeat / counter-own / counter-AI。"""
        counts = [0, 0, 0]
        def predict(h):
            if not h:
                return [1 / 3] * 3
            last = h[-1]
            guesses = [last["player"], COUNTERS[last["player"]], COUNTERS[last["ai"]]]
            out = [0, 0, 0]
            for g, c in zip(guesses, counts):
                out[MOVES.index(g)] += c + self.ALPHA
            return self._norm(out)
        def update(h):
            if len(h) < 2:
                return
            prev, now = h[-2], h[-1]
            if now["player"] == prev["player"]:
                counts[0] += 1
            if now["player"] == COUNTERS[prev["player"]]:
                counts[1] += 1
            if now["player"] == COUNTERS[prev["ai"]]:
                counts[2] += 1
        return {"predict": predict, "update": update}

    # ---- Player 接口 ----
    def _belief(self):
        w = self._norm(self.weights)
        mix = [0.0, 0.0, 0.0]
        for expert, wi in zip(self.experts, w):
            p = expert["predict"](self.hist)
            for i in range(3):
                mix[i] += wi * p[i]
        return self._norm(mix)

    def decide(self, view):
        belief = self._belief()
        ev = {m: sum(belief[i] * outcome(m, MOVES[i]) for i in range(3)) for m in MOVES}
        spread = max(belief) - min(belief)
        if spread < 0.02:                     # 无信号时不猜，随机（避免暴露模式）
            return MOVES[int(self.rng() * 3) % 3]
        if self.mode == "argmax":
            return max(MOVES, key=lambda m: ev[m])
        return _softmax_pick(ev, self.beta, self.rng)

    def observe(self, my_move, opp_move, result):
        # result 是机器人视角；专家内部记人类视角
        human_result = -result
        losses = []
        for expert in self.experts:
            p = expert["predict"](self.hist)          # 用观察前的预测算损失
            losses.append(-math.log(max(p[MOVES.index(opp_move)], 1e-9)))
        self.hist.append({"player": opp_move, "ai": my_move, "result": human_result})
        for expert in self.experts:
            expert["update"](self.hist)
        for i, loss in enumerate(losses):
            self.weights[i] *= math.exp(-self.eta * loss)
        self.weights = self._norm(self.weights)


# ───────────────────── 统一机器人注册点（GUI + 对局循环共用） ─────────────────────

def robot_choices():
    """GUI 下拉用：[(id, 显示名), ...]。所有可选后端只在这里登记一次。"""
    out = []
    for bid, (display, _fn) in S.BACKENDS.items():
        out.append((bid, display))
        if bid == "fusion":                 # Hedge 紧挨 fusion，便于对比
            out.append(("hedge", "Hedge7（7专家+采样）"))
    # 配对后端：只留自适应方向（在线学顺从/逆反），标签中性不泄露方向/权重。
    out += [
        ("pair_adaptive", "后端 A1"),
    ]
    return out


def make_robot(backend, seed=None):
    """后端 id -> 一个有状态/无状态都统一的 Player 实例。

    有状态后端（fusion 的 tracker、hedge 的专家权重、心理战的话术状态）状态
    存在实例内，对局循环只调 decide()/observe()，不再知道里面是什么。
    心理战后端额外有 talk(view) 生成话术，仅供对话窗口显示，不进决策。
    """
    if backend == "hedge":
        return HedgePlayer(seed=seed)
    if backend == "pair_adaptive":
        import fixed_pair as F
        return F.AdaptiveDirectionPlayer(seed=seed)
    return RobotPlayer(backend)


def is_taunting(backend):
    """是否有话术要显示（pair_adaptive 自适应方向后端要显示话）。"""
    return isinstance(backend, str) and backend.startswith("pair_")


def validate_robot(backend):
    valid = {b for b, _ in robot_choices()}
    if backend not in valid:
        raise ValueError(f"未知后端 {backend}，可选: {', '.join(valid)}")


def robot_display(backend):
    return dict(robot_choices()).get(backend, backend)
