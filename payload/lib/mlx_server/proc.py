"""B 层 · 进程原语。有 I/O，无领域知识。"""
import contextlib
import signal
import subprocess

from .result import Ok, Unknown


def started_at_raw(pid, ps_path="/bin/ps", timeout_sec=5):
    # type: (int, str, int) -> object
    """取进程启动时间的原始串。**标量入参，不接 ctx**（断口 ③）。

    接 ctx 会造成循环依赖：构造 Ctx 需要 self_started_at，而取它又要 Ctx。
    `proc.started_at(pid, ctx)` 保留为本函数的薄封装，供构造完成之后使用。

    三态（T-26 取证）：
      rc=0                      → Ok(norm(stdout))
      rc≠0 ∧ stdout空 ∧ stderr空 → Unknown("DEAD")       进程已退出，**可清理**
      rc≠0 ∧ stderr非空          → Unknown("PS_FAILED")  ps 自身失败，**fail-closed 不得动手**

    两个态的处置**相反**，所以用闭集 kind 而不是文本前缀区分（T-26 §二②）。
    """
    try:
        r = subprocess.run(
            [ps_path, "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=timeout_sec, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Unknown("TOOL_UNAVAILABLE", str(e))
    if r.returncode == 0:
        return Ok(r.stdout.strip())          # normalize 只有这一处（INV-20）
    if not r.stdout.strip() and not r.stderr.strip():
        return Unknown("DEAD")
    return Unknown("PS_FAILED", r.stderr.strip())


# ── 信号屏蔽窗口（INV-07 / G-RES-09）──────────────────────────────

@contextlib.contextmanager
def masked():
    """屏蔽 INT/TERM 的窗口。**全项目唯一的 `pthread_sigmask` 实现点**。

    调用点集合是**三元相等断言**（`G-RES-09(b)`）：
    `spawn` 的交接段 · `atomic.lock` 的临界区 · `supervisor.down` 的停止段。

    它关闭的是一个真实发生过的事故（E-7）：180 秒就绪等待里按 Ctrl-C，
    shell 退出而后台 python 继续把 28 GB 读进内存 —— 因为 spawn 与「把 pid
    记下来」之间被信号截断，没有人再知道该去杀谁。

    退出时恢复**进入前的掩码**，不是恒为空集 —— 嵌套调用时后者会把外层的
    屏蔽也解掉。共同判据只有一条（J-2）：**窗口内不得存在没有上界的停留**。
    """
    try:
        prev = signal.pthread_sigmask(signal.SIG_BLOCK,
                                      {signal.SIGINT, signal.SIGTERM})
    except (AttributeError, OSError):
        # 平台不支持 ⇒ 不屏蔽也要让窗口体跑完（功能不能因此不可用）
        yield
        return
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, prev)


def reset_child_mask():
    """**子进程侧恢复默认掩码**（BC-C-05）。用作 `Popen(preexec_fn=...)`。

    设计逐字：「`exec` 保留掩码、只重置处理器 —— 不做这一步，后端将
    **永久屏蔽 INT/TERM**，`down` 的 TERM 阶梯失效，
    T3 第 3 行的『KILL 阶梯走完仍存活』会从异常变成常态」。

    也就是说：只加 `masked()` 而漏掉这一步，会让**所有后端都杀不掉** ——
    比不做屏蔽严重得多。这个函数与 `masked()` 必须同批落地。
    """
    with contextlib.suppress(AttributeError, OSError):
        signal.pthread_sigmask(signal.SIG_SETMASK, set())
