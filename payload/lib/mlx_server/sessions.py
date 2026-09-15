"""C 层 · 会话文件与 REPL 历史。权限 700/600，显式保存，恢复精确匹配。"""
import json
import os
import time


def _dir(ctx):
    d = os.path.join(ctx.root_data, "sessions")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def new(ctx, service):
    # type: (object, str) -> str
    """新建会话，返回 sid。"""
    sid = f"{service}-{int(time.time() * 1000)}"
    p = os.path.join(_dir(ctx), f"{sid}.json")
    from . import atomic
    with atomic.replace(p, mode=0o600) as fh:
        fh.write(json.dumps({"sid": sid, "service": service, "turns": []},
                            ensure_ascii=False))
    return sid


def load(ctx, sid):
    # type: (object, str) -> object
    """**精确匹配** —— 不做前缀模糊，避免恢复错会话。"""
    p = os.path.join(_dir(ctx), f"{sid}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.loads(fh.read())


def save(ctx, sid, turns):
    # type: (object, str, list) -> None
    """**显式保存** —— 不做自动落盘，避免用户以为没记录却留了痕。"""
    p = os.path.join(_dir(ctx), f"{sid}.json")
    obj = load(ctx, sid) or {"sid": sid, "service": None}
    obj["turns"] = list(turns)
    from . import atomic
    with atomic.replace(p, mode=0o600) as fh:
        fh.write(json.dumps(obj, ensure_ascii=False))


def history_path(ctx):
    # type: (object) -> str
    """REPL 历史文件。权限 600。"""
    p = os.path.join(_dir(ctx), ".history")
    if not os.path.exists(p):
        open(p, "a").close()
        os.chmod(p, 0o600)
    return p
