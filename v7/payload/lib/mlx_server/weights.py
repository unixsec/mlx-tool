"""C 层 · 权重根。**`revision` 的唯一产者**。

`size()` 是**实际文件列表求和**，不是 `du` —— HF snapshot 全是符号链接，
`du` 对它恒返 0（T-07）。
"""
import contextlib
import os

from . import vault
from .result import Done, Failed, Ok, Refused, Unknown


class DiskFacts:
    """磁盘事实扫描结果。"""

    __slots__ = ("installed", "orphans", "total_bytes")

    def __init__(self, installed, orphans, total_bytes):
        self.installed = list(installed)
        self.orphans = list(orphans)
        self.total_bytes = total_bytes

    def is_unknown(self):
        # type: () -> bool
        """`scan_installed` 现在返回 `DiskFacts | Unknown` —— 两个分支都要能被
        **同一个判定词** `result.is_unknown()` 处理（裁决 D-10：那是判定路径上
        唯一允许的写法）。缺了这个方法，成功分支会在判定处 AttributeError。"""
        return False


def size(path):
    # type: (str) -> object
    """体积求和。**逐文件 lstat 求和，跟随符号链接到 blob**。

    坏样本：用 `du` —— HF 的 snapshot 目录全是指向 blobs/ 的符号链接，
    `du` 只算链接本身（每个 ~100 字节）⇒ 一个 28GB 的模型报成几 KB
    ⇒ 容量判档永远 FIT ⇒ 装第二个模型时磁盘爆掉。
    """
    if not os.path.exists(path):
        return Unknown("PARSE_FAILED", f"路径不存在：{path}")
    # **读不动的东西不能当成 0 字节。**
    # 上面那段注释记着「用 du 会让容量判档永远 FIT」的教训，而这里原本留着
    # 同一个洞的另外两半：`os.walk` 对不可读目录**静默跳过**（onerror 默认 None），
    # `except OSError: continue` 又吞掉 stat 失败。结果是一个权限不对的
    # 28 GB 模型报成 0 字节 ⇒ `size_mb: 0.0` 写进记录 ⇒ 判档照样恒 FIT。
    # 探测失败必须以 `Unknown` 的形态往上走，让上层 fail-closed。
    total, seen, failed = 0, set(), []
    for dirpath, _, files in os.walk(path, onerror=failed.append):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                real = os.path.realpath(fp)
                if real in seen:                  # blob 去重：多个 snapshot 共享同一 blob
                    continue
                seen.add(real)
                total += os.stat(fp).st_size      # stat 跟随链接 ⇒ 拿到 blob 真实大小
            except OSError as exc:
                failed.append(exc)
    if failed:
        return Unknown("TOOL_UNAVAILABLE",
                       f"{len(failed)} 处读不了（首个：{failed[0]}）")
    return Ok(total)


def resolve(ctx, repo, revision=None):
    # type: (object, str, str) -> object
    """三布局解析：HF 缓存 / 扁平目录 / 直接路径。`revision` 的唯一产者。"""
    root = ctx.root_weights
    cache_name = "models--" + repo.replace("/", "--")      # HF 缓存布局
    for layout, base in (("hf", os.path.join(root, "hub", cache_name)),
                         ("flat", os.path.join(root, repo.replace("/", "--"))),
                         ("direct", os.path.join(root, repo))):
        if not os.path.isdir(base):
            continue
        if layout == "hf":
            snap = os.path.join(base, "snapshots")
            if not os.path.isdir(snap):
                continue
            revs = sorted(os.listdir(snap))
            if not revs:
                continue
            rev = revision if revision in revs else revs[-1]
            return Ok({"layout": layout, "path": os.path.join(snap, rev), "revision": rev})
        return Ok({"layout": layout, "path": base, "revision": revision or "unknown"})
    # **返回类型必须与成功路径一致**（`Probe`）。此前这里返回 `Outcome`，
    # 于是 `resolve` 一个函数返回两个正交类型 —— 调用方写 `r.rc` 就在
    # 找到权重时崩（Probe 没有 .rc），写 `r.value` 就在找不到时崩。
    # 「不在盘上」是确定的**否定事实**，不是探测失败，故 `Ok(None)`。
    return Ok(None)


def scan_installed(ctx):
    # type: (object) -> DiskFacts
    """磁盘事实扫描：装了什么、有没有孤儿。**不读注册表** —— 这是磁盘侧的真相。"""
    root = ctx.root_weights
    installed, total = [], 0
    hub = os.path.join(root, "hub")
    if os.path.isdir(hub):
        for d in sorted(os.listdir(hub)):
            if not d.startswith("models--"):
                continue
            s = size(os.path.join(hub, d))
            if s.is_unknown():
                # **不塌成 0**。契约：「扫描不完整 ⇒ `Unknown` ⇒ 上层拒绝重建，
                # 不用部分事实覆盖记录」。塌成 0 的后果现在更重：
                # `size_mb: 0.0` 会写进记录，而容量判档读的就是它 ——
                # 一个 28 GB 的模型会被判成 FIT，闸门形同虚设。
                return Unknown(s.kind, f"{d}: {s.reason}")
            b = s.value
            # 字段名按 `DiskFacts{revision, size_mb, blobs}` 的契约（`02:479`）——
            # 此前产的是 `bytes`，而 `rebuild_machine` 找的是 `size_mb`，
            # 两边名字对不上 ⇒ 体积那一格永远填不上、永远显示「—」。
            snap_dir = os.path.join(hub, d, "snapshots")
            revs = sorted(os.listdir(snap_dir)) if os.path.isdir(snap_dir) else []
            installed.append({"dir": d, "repo": d[len("models--"):].replace("--", "/"),
                              "bytes": b, "size_mb": round(b / (1024 * 1024), 1),
                              "revision": revs[-1] if revs else None})
            total += b
    return DiskFacts(installed, [], total)


def _dir_bytes(path):
    # type: (str) -> int
    """目录里已有多少字节。断点续传时用来报「已下 X GB」。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, f))
    return total


def download(ctx, repo, revision=None, fetch=None, announce=None):
    # type: (object, str, str, object) -> object
    """下载。`fetch` 是注入点（L2 用桩，L4 用真 hf）。**先写临时目录再原子改名。**"""
    if fetch is None:
        return Refused(9, "weights.no_fetcher",
                       detail="download 的 fetch 未注入 —— 产品不自带下载器实现")
    # 临时目录名**由 repo+revision 派生，不带 pid**。带 pid 的后果是每次
    # 重试都换一个新目录 ⇒ 下载器无从续传 ⇒ 28 GB 下到 26 GB 断网，
    # 重来一次从 0 开始。设计对 `download` 失败模式的原话是「按下载前后的
    # blob 集合差回滚，**已完成的 blob 保留**」—— 稳定的目录名是它的前提。
    slug = repo.replace("/", "--")
    tmp = os.path.join(ctx.root_weights, f".mlxtmp.{slug}.{revision or 'main'}")
    os.makedirs(tmp, exist_ok=True)

    # **开始前先说清楚要下多少、下到哪**。`hf` 自己的输出里没有总量，
    # 用户等一个 30 GB 的下载，最想知道的恰恰是「一共多大、还剩多少」。
    # 断点续传时同一目录里已有的部分也报出来 —— 否则重跑看起来像从头开始。
    if announce is not None:
        already = _dir_bytes(tmp)
        announce(repo, already)

    out = fetch(repo, revision, tmp) or {}
    # **fetch 的成败信号必须读。** 此前是 `return Done(fetch(...))` ——
    # 无条件包成 Done ⇒ `hf` 不存在时也报「下载成功」，而权重根空空如也。
    # 这是「报成功但没产生效果」最直白的一种：用户以为模型下好了。
    if not out.get("ok"):
        reason = str(out.get("reason") or "")
        key = ("weights.no_downloader"
               if ("tool_unavailable" in reason or "No such file" in reason)
               else "weights.download_failed")
        # **用 `Failed` 不用 `Refused`**：本函数在 `model install` 的
        # stage_split（第一次 commit）之后被调用，`Refused` 的前提
        # 「零资源已创建」不成立 —— L1 守卫 `result-02` 会当场抓住。
        # 附录 A 给 `model install` 的码含 3（外部依赖不可用）。
        # **失败时不删临时目录** —— 已下完的部分留着，下次重试从这里接上。
        return Failed(3, key, [], repo=repo,
                      detail=f"{reason[:160]}（已下载的部分保留在 {tmp}，"
                             f"重跑同一条命令会接着下）")

    # ── 原子改名到**规划目录** ──────────────────────────────────
    # docstring 一直写着「先写临时目录再原子改名」，而**改名那一步此前不存在**：
    # 下载完权重留在 `.mlxtmp.download/`，而 `resolve()` 找的是
    # `hub/models--<组织>--<模型>/snapshots/<rev>/` ⇒ 下完也解析不到，
    # 状态永远停在「已登记未下载」。用户看到的是「下载成功但没生效」。
    rev = out.get("revision") or revision or "main"
    dest = os.path.join(ctx.root_weights, "hub",
                        "models--" + repo.replace("/", "--"), "snapshots", rev)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(dest):
            # 已存在同 revision：新下的作废，保留原有（`revision` 是内容地址，
            # 同 rev 内容相同；覆盖只会制造一个中间态窗口）。
            # **经 vault** —— INV-08 要求递归删除只有一个实现点；此前这里
            # 手写了第二份（`os.walk` + `os.remove`），它躲开了守卫的 AST
            # 提取面（那面只找 `shutil.rmtree`）却违反了不变式本身。
            vault.trash(ctx, tmp, None)
        else:
            os.replace(tmp, dest)      # 同盘改名，原子
    except OSError as exc:
        return Failed(1, "weights.place_failed", [], repo=repo,
                      detail=f"{exc}（下载的文件在 {tmp}）")
    # **成功路径的 return。** 它曾经被一次「按硬编码行号切文件」的批量删除
    # 连带切掉（`539c2f2`），函数于是走完隐式返回 `None`，调用方
    # `out.rc` 当场 AttributeError —— 而**下载成功**才走得到这里，
    # 测试环境没有真 `hf`，这条路径一次都没被执行过，150 项全绿。
    return Done(dict(out, path=dest, revision=rev))
