"""C 层 · **全项目唯一的递归删除实现点**。

前缀断言 + 白名单拒绝 + **原路径记录** + 保留期/容量/触发三参数。
"""
import contextlib
import json
import os
import shutil
import time

from .result import Done, Failed, Refused


class PathRefused(Exception):
    """路径不在授权面内。**白名单拒绝**：空串 / `/` / 家目录 / 相对路径 / 含上跳。"""


def assert_under(root, p):
    # type: (str, str) -> None
    """纯词法比较。**调用方负责先 `resolve()`**（本函数不碰文件系统，保持零 I/O）。"""
    r, q = str(root).rstrip("/"), str(p).rstrip("/")
    if not q.startswith(r + "/") and q != r:
        raise PathRefused(f"{p} 不在 {root} 之内")


def _white_list_check(p):
    s = str(p)
    if not s or s == "/" or s == os.path.expanduser("~"):
        raise PathRefused(f"拒绝删除 {s!r}（空串 / 根 / 家目录）")
    if not os.path.isabs(s):
        raise PathRefused(f"拒绝相对路径 {s!r}")
    if ".." in s.split("/"):
        raise PathRefused(f"路径含上跳 {s!r}")


def trash(ctx, path, stack=None):
    # type: (object, str, object) -> object
    """移入回收站并**记录原路径**（唯一的可恢复删除）。"""
    _white_list_check(path)
    p = os.path.realpath(path)
    for root in (ctx.root_weights, ctx.root_data):
        try:
            assert_under(os.path.realpath(root), p)
            break
        except PathRefused:
            continue
    else:
        raise PathRefused(f"{path} 不在权重根或数据根之内 —— trash 不接受整根")

    tdir = trash_dir(ctx)
    os.makedirs(tdir, exist_ok=True)
    stamp = str(int(time.time() * 1000))
    dest = os.path.join(tdir, f"{os.path.basename(p)}.{stamp}")
    os.rename(p, dest)
    _append_index(ctx, {"id": os.path.basename(dest), "origin": p, "at": stamp})
    return Done({"trashed": dest, "origin": p})


def trash_dir(ctx):
    return os.path.join(ctx.root_data, "trash")


def _index_path(ctx):
    # 台账落点按 `02-architecture.md:402` 与 T2 第 22 行：`data/trash/index.jsonl`。
    # 早期实现写的是 `origins.tsv` —— `_read_index` 会把它迁过来，不丢数据。
    return os.path.join(trash_dir(ctx), "index.jsonl")


def _append_index(ctx, entry):
    # type: (object, dict) -> None
    """追加一条回收记录。**权限 0o600**（`C-SUP-T54'` 的三条相等断言之一）。

    理由不是「怕别人看」，是这份台账是 `restore` 的**目标路径来源** ——
    另一个本机进程改一个字节就能改变一次移动操作的落点。
    """
    path = _index_path(ctx)
    existing = os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    if not existing:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def _read_index(ctx):
    # type: (object) -> list
    """读回收台账。**一次性迁移早期的 `origins.tsv`** —— 那是同一份数据的旧格式，
    直接无视它会让用户已经删进回收站的东西永远恢复不了。
    """
    tdir = trash_dir(ctx)
    entries, seen = [], set()
    path = _index_path(ctx)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue                       # 坏行跳过，不让一行毁掉整份台账
                if isinstance(obj, dict) and obj.get("id") and obj["id"] not in seen:
                    seen.add(obj["id"])
                    entries.append(obj)
    legacy = os.path.join(tdir, "origins.tsv")
    if os.path.exists(legacy):
        with open(legacy, encoding="utf-8") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 2 and cols[0] and cols[0] not in seen:
                    seen.add(cols[0])
                    entries.append({"id": cols[0], "origin": cols[1],
                                    "at": cols[2] if len(cols) > 2 else ""})
    return entries


def entries(ctx):
    # type: (object) -> list
    """回收站里现存的条目（**台账 × 磁盘取交集**）。

    只报台账会列出已经被 `empty` 掉的东西；只报磁盘则不知道原路径 ——
    `restore` 两样都要。
    """
    tdir = trash_dir(ctx)
    out = []
    for e in _read_index(ctx):
        p = os.path.join(tdir, e["id"])
        if os.path.exists(p):
            out.append(dict(e, path=p, size_mb=_size_mb(p)))
    return out


def _size_mb(path):
    total = 0
    if os.path.isfile(path):
        total = os.path.getsize(path)
    else:
        for root, _dirs, files in os.walk(path):
            for f in files:
                with contextlib.suppress(OSError):
                    total += os.path.getsize(os.path.join(root, f))
    return round(total / (1024 * 1024), 1)


def restore(ctx, entry_id):
    # type: (object, str) -> object
    """恢复到**原路径**。原路径已被占用 ⇒ 拒绝，不覆盖。"""
    match = [e for e in entries(ctx) if e["id"] == entry_id]
    if not match:
        return Refused(6, "trash.not_found", id=entry_id)
    e = match[0]
    origin = e["origin"]
    if os.path.exists(origin):
        return Refused(7, "trash.origin_occupied", id=entry_id, origin=origin)
    _white_list_check(origin)
    try:
        os.makedirs(os.path.dirname(origin), exist_ok=True)
        os.rename(e["path"], origin)
    except OSError as exc:
        return Failed(1, "trash.restore_failed", [], id=entry_id, detail=str(exc))
    return Done({"kind": "trash_restore", "restored": entry_id, "origin": origin})


def purge(ctx, path):
    # type: (object, str) -> object
    """**不可恢复的递归删除**。整根支只允许四个成员（裁决 R1-2）：

    `ctx.venv` / `root_weights` / `root_data` / `root_data/trash`。
    **`ctx.root_code` 不在集合内**（INV-08）—— 代码根的清理不需要递归。
    """
    _white_list_check(path)
    p = os.path.realpath(path)
    roots = {os.path.realpath(getattr(ctx, "venv", "/nonexistent")),
             os.path.realpath(ctx.root_weights),
             os.path.realpath(ctx.root_data),
             os.path.realpath(os.path.join(ctx.root_data, "trash"))}
    if p == os.path.realpath(ctx.root_code):
        return Refused(6, "vault.code_root_not_purgeable",
                       detail="代码根不在整根删除授权集合内（INV-08）")
    if p not in roots:
        for r in (ctx.root_weights, ctx.root_data):
            try:
                assert_under(os.path.realpath(r), p)
                break
            except PathRefused:
                continue
        else:
            return Refused(6, "vault.not_authorized", path=p)
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.exists(p):
        os.unlink(p)
    return Done({"purged": p})
