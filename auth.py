"""本地用户认证：注册/登录/当前会话。

安全：密码用 PBKDF2-HMAC-SHA256 + 每用户随机盐（16 字节），绝不存明文。
不往日志/stdout 打印密码。存储 users.json，权限靠本地文件系统。

将来要换外部身份服务，只改本文件（auth.py），上层只认 user_id 字符串。
"""
import hashlib
import hmac
import json
import os
import secrets
import time

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "users.json")
ITERATIONS = 200_000


class AuthError(Exception):
    pass


def _load():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_FILE)   # 原子写，防半写


def _hash(password, salt_hex, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(salt_hex), iterations,
    ).hex()


def register(username, password):
    """注册新用户。用户名非空且唯一；密码至少 6 位。返回 user_id。"""
    username = username.strip()
    if not username:
        raise AuthError("用户名不能为空")
    if len(password) < 6:
        raise AuthError("密码至少 6 位")
    users = _load()
    if username in users:
        raise AuthError("用户名已存在")
    salt = secrets.token_hex(16)
    users[username] = {
        "user_id": username,          # 本地方案：用户名即 id；换外部服务时改成独立 id
        "salt": salt,
        "pwd_hash": _hash(password, salt),
        "iterations": ITERATIONS,
        "created_at": time.time(),
    }
    _save(users)
    return username


def login(username, password):
    """登录成功返回 user_id；失败抛 AuthError（不区分用户不存在/密码错，防枚举）。"""
    username = username.strip()
    users = _load()
    rec = users.get(username)
    if rec is None:
        raise AuthError("用户名或密码错误")
    calc = _hash(password, rec["salt"], rec.get("iterations", ITERATIONS))
    if not hmac.compare_digest(calc, rec["pwd_hash"]):
        raise AuthError("用户名或密码错误")
    return rec["user_id"]


def list_users():
    return sorted(_load().keys())


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="用户管理（本地）")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register"); r.add_argument("username"); r.add_argument("password")
    l = sub.add_parser("login"); l.add_argument("username"); l.add_argument("password")
    sub.add_parser("list")
    a = p.parse_args()
    if a.cmd == "register":
        print("注册成功:", register(a.username, a.password))
    elif a.cmd == "login":
        print("登录成功:", login(a.username, a.password))
    else:
        print("用户列表:", list_users())
