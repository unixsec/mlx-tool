"""C 层 · **唯一的原子替换 + 锁 + 备份实现点**（不是「唯一落盘点」）。

拥有：`.mlxtmp.` 前缀命名空间 · 锁目录 · 备份命名空间。
`G-RES-18`：锁目录的创建/删除/写入调用点集合 **== {atomic}**。
"""
import contextlib
import os
import time

from .result import is_unknown

TMP_PREFIX = ".mlxtmp."


class LockTimeout(Exception):
    """获取锁超时。`holder_pid` 供调用方指名持有者。"""

    def __init__(self, path, holder_pid=None):
        super().__init__(f"锁被占用：{path}（持有者 pid={holder_pid}）")
        self.path = path
        self.holder_pid = holder_pid


class StaleLockUnknown(Exception):
    """陈旧锁判定为 `Unknown` ⇒ **fail-closed，不清理、不动手**。"""


@contextlib.contextmanager
def replace(target, mode=None):
    """原子替换：写临时文件 → `os.replace`。**唯一实现点**。

    用法：`with atomic.replace(p) as tmp: tmp.write(...)`

    `mode` 给定时（如 `0o600`），临时文件**以该权限创建**，替换后再对目标
    `chmod` 一次。只在替换后 chmod 会留下一个 0644 的窗口（密钥在窗口里可读）；
    只在创建时设又挡不住「目标原本就更宽」—— 两处都要。
    """
    d = os.path.dirname(os.path.abspath(target))
    tmp = os.path.join(d, f"{TMP_PREFIX}{os.path.basename(target)}.{os.getpid()}")
    if mode is None:
        # noqa 理由：本函数是 contextmanager，fh 的生命周期**跨 yield**，
        # 用 with 会在 yield 前关闭它。关闭与清理由下面的 try/except 承担。
        fh = open(tmp, "w")  # noqa: SIM115
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)                       # 上次崩溃留下的同名临时文件
        fh = os.fdopen(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode), "w")
    try:
        yield fh
        fh.close()
        os.replace(tmp, target)          # 同盘 rename，原子
        if mode is not None:
            os.chmod(target, mode)
    except BaseException:
        fh.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def lock(name, stack, ctx, timeout_sec=10, poll_sec=0.05, owns=None):
    """目录锁。临界区整体应在 `proc.masked()` 内（本片尚未接信号屏蔽）。

    **⓪ 写内容文件之前先消费 `ctx.self_started_at`**（裁决 R4-4）：
    `is_unknown(...)` ⇒ 释放锁并抛，**绝不写入占位符** ——
    写 `""` 会让下一个进程把「记录过期」误判成 RECYCLED 而清掉活进程的锁。
    """
    lockdir = os.path.join(os.path.dirname(os.path.abspath(name)),
                           f".{os.path.basename(name)}.lock")
    deadline = time.time() + timeout_sec
    while True:
        try:
            os.mkdir(lockdir)            # 原子：EEXIST 即已被占
            break
        except FileExistsError:
            holder = _read_holder(lockdir)
            if holder is not None and owns is not None:
                st = owns(holder)
                if is_unknown(st) and not st.is_dead():
                    raise StaleLockUnknown(
                        f"锁持有者 {holder} 的所有权不可证（{st.kind}）⇒ fail-closed，不清理"
                    ) from None
                if is_unknown(st) and st.is_dead():
                    _force_release(lockdir)      # 确知已死才清
                    continue
            if time.time() >= deadline:
                raise LockTimeout(lockdir, holder) from None
            time.sleep(poll_sec)

    handle = stack.push(lambda: _force_release(lockdir)) if stack else None
    # ── 屏蔽窗口（`G-RES-09(b)` 的三个调用点之一）─────────────────
    # 覆盖「写 holder → 让出临界区 → 释放锁」整段。中间被 TERM 打断的后果是
    # **锁目录留在盘上而持有者已经不在** —— 陈旧锁清理能收拾它（已接线），
    # 但下一条写命令要先等满 10 秒才轮到清理，那段等待是白等的。
    # 更坏的一半：`atomic.replace` 的临时文件与 rename 之间被截断，
    # 留下一个半写的 `.tmp`。
    # 窗口内的可阻塞调用集合为空（只有有界 syscall：write / rename / rmdir）。
    from . import proc as _proc
    with _proc.masked():
        try:
            st = getattr(ctx, "self_started_at", None)
            if st is not None and is_unknown(st):
                raise StaleLockUnknown(
                    f"本进程的 started_at 不可证（{st.kind}）⇒ 不写内容文件、释放锁"
                )
            with open(os.path.join(lockdir, "holder"), "w") as fh:
                fh.write(f"{os.getpid()}\n{st.value if st is not None else ''}\n")
            yield lockdir
        finally:
            if handle is not None:
                stack.run(handle)
            else:
                _force_release(lockdir)


def _read_holder(lockdir):
    try:
        with open(os.path.join(lockdir, "holder")) as fh:
            first = fh.readline().strip()
        return int(first) if first.isdigit() else None
    except (OSError, ValueError):
        return None


def _force_release(lockdir):
    with contextlib.suppress(OSError):
        p = os.path.join(lockdir, "holder")
        if os.path.exists(p):
            os.unlink(p)
        os.rmdir(lockdir)
