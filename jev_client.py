"""Jev 决策层：经 magpie 网关调 TypeSafe Jev（System One 决策模型）。

协议（官方）：POST {gateway}/v1/systemone
  请求 {model, state, questions:{id:{type,instructions,criteria}}}
  choice 返回 {type,choice,confidence,probabilities}

模型走 magpie 的 zensystemone 池：jev-1.13(付费) / jev-1.13-free(免费)。
网关 key：短期 key 由 `magpie gateway-key issue --models zensystemone/*` 签发，
存在 .magpie_key（不在 git 里）。

轮层用法：把对手历史摘要成 state，问一个 choice 问题，拿 choice + confidence。
confidence 低时调用方可以退回统计策略（见 strategy.py 的 escalation）。
"""
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, ".magpie_key")
GATEWAY = os.environ.get("MAGPIE_GATEWAY", "http://127.0.0.1:3430")
# 默认走付费档：free 档限流后会让每轮都失败重试，反而烧时间
MODEL = os.environ.get("JEV_MODEL", "zensystemone/jev-1.13")

# ── 熔断：防止网关/模型故障时无限重试把额度烧光 ──
MAX_CONSECUTIVE = 3      # 连续失败这么多次 → 熔断
COOLDOWN = 60.0          # 熔断多久（秒）
MAX_TOTAL = 10           # 本进程累计失败这么多次 → 永久停用

_breaker = {
    "consecutive": 0,
    "total": 0,
    "open_until": 0.0,
    "disabled": False,
}


def breaker_state():
    """给 GUI/日志看：当前熔断状态。"""
    now = time.monotonic()
    if _breaker["disabled"]:
        return "disabled"
    if now < _breaker["open_until"]:
        return f"open({_breaker['open_until'] - now:.0f}s)"
    return "closed"

MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
COUNTERS = {v: k for k, v in BEATS.items()}   # 克制 x 的拳


class JevError(Exception):
    pass


def _key():
    if not os.path.exists(KEY_FILE):
        raise JevError(
            "没有网关 key。先签一个：\n"
            "  magpie gateway-key issue --ttl 1h --name rps-jev "
            "--models \"zensystemone/*\" --from 242b0535f32f46e71240a241 --json\n"
            "然后把返回的 key 写进 .magpie_key"
        )
    with open(KEY_FILE, encoding="utf-8") as f:
        return f.read().strip()


def ask_choice(state, question, criteria, model=None, timeout=30):
    """问一个 choice 问题。返回 {choice, confidence, probabilities, usage}。

    带熔断：连续/累计失败达到阈值就不再发请求，避免故障时烧额度。
    """
    now = time.monotonic()
    if _breaker["disabled"]:
        raise JevError("Jev 已停用（本进程累计失败过多）")
    if now < _breaker["open_until"]:
        raise JevError(f"Jev 熔断中，{_breaker['open_until'] - now:.0f}s 后恢复")

    payload = {
        "model": model or MODEL,
        "state": state,
        "questions": {
            "q": {
                "type": "choice",
                "instructions": question,
                "criteria": criteria,
            }
        },
    }
    req = urllib.request.Request(
        GATEWAY + "/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + _key(),
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _record_fail()
        raise JevError(f"网关 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except Exception as e:
        _record_fail()
        raise JevError(f"网关调用失败: {type(e).__name__} {e}") from e

    ans = body.get("answers", {}).get("q")
    if not ans:
        _record_fail()
        raise JevError(f"返回里没有 answers.q: {json.dumps(body, ensure_ascii=False)[:200]}")

    _breaker["consecutive"] = 0      # 成功一次就清零连击
    return {
        "choice": ans.get("choice"),
        "confidence": ans.get("confidence"),
        "probabilities": ans.get("probabilities", {}),
        "usage": body.get("usage", {}),
    }


def _record_fail():
    """记一次失败，超阈值就熔断/停用。"""
    _breaker["consecutive"] += 1
    _breaker["total"] += 1
    if _breaker["total"] >= MAX_TOTAL:
        _breaker["disabled"] = True
        print(f"[jev] 累计失败 {_breaker['total']} 次，本进程停用 Jev", flush=True)
    elif _breaker["consecutive"] >= MAX_CONSECUTIVE:
        _breaker["open_until"] = time.monotonic() + COOLDOWN
        print(f"[jev] 连续失败 {_breaker['consecutive']} 次，熔断 {COOLDOWN:.0f}s",
              flush=True)


def decide_move(history, mode_hint="", min_confidence=0.0, model=None):
    """轮层决策：给定对手出拳历史，返回 (move, confidence, raw)。

    问 Jev「对手下一手最可能出什么」→ 机械手出克它的拳。
    confidence < min_confidence 时 move 为 None，交给调用方退回统计策略。
    """
    recent = history[-10:]
    freq = {m: recent.count(m) for m in MOVES}
    state = (
        "石头剪刀布对局分析。对手（用户）最近出拳序列：" + str(recent) +
        f"。频次统计：{freq}。" +
        (f"本局模式：{mode_hint}。" if mode_hint else "")
    )
    criteria = {
        "rock": "石头（克制剪刀）",
        "scissors": "剪刀（克制布）",
        "paper": "布（克制石头）",
    }
    r = ask_choice(state, "根据对手历史，对手下一手最可能出什么？", criteria, model=model)
    picked = r["choice"]
    if picked not in MOVES:
        raise JevError(f"Jev 返回了非法选项: {picked}")
    move = COUNTERS[picked]          # 出克它的拳
    conf = r["confidence"]
    if conf is not None and conf < min_confidence:
        return None, conf, r
    return move, conf, r


if __name__ == "__main__":
    import sys
    hist = sys.argv[1:] or ["scissors", "scissors"]
    m, c, raw = decide_move(hist)
    print(json.dumps({"history": hist, "move": m, "confidence": c,
                      "probabilities": raw["probabilities"],
                      "usage": raw["usage"]}, ensure_ascii=False, indent=2))
