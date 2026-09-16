"""B 层 · **外部命令**的三态封装。命令路径经 `ctx` 解析。

`Unknown` 的 kind 是**闭集**（`result.UNKNOWN_KINDS`）—— 不用自由文本区分态。
"""
import subprocess

from .result import Ok, Unknown


def _run(argv, timeout_sec):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return False


def listener_pids(port, lsof_path="/usr/sbin/lsof", timeout_sec=5):
    # type: (int, str, int) -> object
    """端口上**全部**监听进程的 pid（任意地址：`0.0.0.0`、`127.0.0.1`、`::`）。

    此前只取第一个（spec §0.7-9）：`0.0.0.0:P` 与 `127.0.0.1:P` 可以分属两个进程同时监听，
    而回环连接落到后者 —— 只看第一个 pid 会把「端口上还有别人」判成「是我们的」。

    三态（T-26 同族）：
      rc=0            → Ok([pid, ...])
      rc≠0 ∧ stderr空 → Unknown("NO_LISTENER")   **确知无人监听**
      rc≠0 ∧ stderr非空 → Unknown("PS_FAILED")   lsof 自身失败，**不得当作无人监听**
    """
    r = _run([lsof_path, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout_sec)
    if r is None:
        return Unknown("TIMEOUT")
    if r is False:
        return Unknown("TOOL_UNAVAILABLE")
    if r.returncode == 0 and r.stdout.strip():
        pids = []
        for line in r.stdout.split():
            if not line.isdigit():
                return Unknown("PARSE_FAILED", line[:40])
            if int(line) not in pids:
                pids.append(int(line))
        return Ok(pids)
    if not r.stderr.strip():
        return Unknown("NO_LISTENER")
    return Unknown("PS_FAILED", r.stderr.strip()[:80])


def rss_kb(pid, ps_path="/bin/ps", timeout_sec=5):
    # type: (int, str, int) -> object
    """进程的常驻内存（KB）。`Probe[int]`。

    `health.model_memory`（`02:1833`「运行中模型的实际占用」）的数据来源。
    外部命令一律经本模块封装（§1.4：`probe` 是外部命令的唯一封装点）。
    """
    r = _run([ps_path, "-o", "rss=", "-p", str(pid)], timeout_sec)
    if r is None:
        return Unknown("TIMEOUT", f"{ps_path} 超时")
    if r is False:
        return Unknown("TOOL_UNAVAILABLE", ps_path)
    if r.returncode != 0:
        # 进程已退出：stdout 与 stderr 都空（T-26 的四态判定同源）
        if not r.stdout.strip() and not r.stderr.strip():
            return Ok(None)
        return Unknown("PS_FAILED", r.stderr.strip()[:80])
    txt = r.stdout.strip()
    return Ok(int(txt)) if txt.isdigit() else Unknown("PARSE_FAILED", txt[:40])


def pgid_of(pid, ps_path="/bin/ps", timeout_sec=5):
    # type: (int, str, int) -> object
    """进程组 id。

    **字段宽度固定 5、右对齐** ⇒ pgid 位数 <5 时**有前导空格**
    （`BCR-4-04` 因样本偏差被撤销）。`int()` 前必经 `.strip()`；
    **不得以「解析失败」为坏样本** —— `int(b" 6477\\n")` 不抛异常。
    """
    r = _run([ps_path, "-o", "pgid=", "-p", str(pid)], timeout_sec)
    if r is None:
        return Unknown("TIMEOUT")
    if r is False:
        return Unknown("TOOL_UNAVAILABLE")
    if r.returncode == 0:
        raw = r.stdout.strip()
        if not raw.isdigit():
            return Unknown("PARSE_FAILED", raw[:40])
        return Ok(int(raw))
    if not r.stderr.strip():
        return Unknown("DEAD")
    return Unknown("PS_FAILED", r.stderr.strip()[:80])


def children_of(pid, pgrep_path="/usr/bin/pgrep", timeout_sec=5):
    # type: (int, str, int) -> object
    """直接子进程 pid 元组。

    `Ok(())` 是「**确知没有**」，与 `Unknown` 不同 ——
    把「无子进程」判成 `Unknown` 会让 `kill_tree` 每次都退化。
    """
    r = _run([pgrep_path, "-P", str(pid)], timeout_sec)
    if r is None:
        return Unknown("TIMEOUT")
    if r is False:
        return Unknown("TOOL_UNAVAILABLE")
    if r.returncode == 0:
        return Ok(tuple(int(x) for x in r.stdout.split() if x.isdigit()))
    if not r.stderr.strip():
        return Ok(())                  # rc=1 + 无 stderr ⇒ 确知无子进程
    return Unknown("PS_FAILED", r.stderr.strip()[:80])


def available_mem_mb(vm_stat_path="/usr/bin/vm_stat", timeout_sec=5):
    # type: (str, int) -> object
    """当前**可分配**内存（MB）。三态。

    macOS 没有 Linux 的 MemAvailable，用 `vm_stat` 的
    free + inactive + speculative + purgeable 近似 —— 这几类都是内核
    在压力下可以立刻回收的页。**不算 wired 与 active**。

    探不到时返回 `Unknown` 而不是某个数：判档是 fail-closed 的闸门，
    「内存不明」与「内存够」的处置完全相反。
    """
    r = _run([vm_stat_path], timeout_sec)
    if r is None:
        return Unknown("TIMEOUT", "vm_stat 超时")
    if r is False:
        return Unknown("TOOL_UNAVAILABLE", f"跑不起来 {vm_stat_path}")
    if r.returncode != 0:
        return Unknown("TOOL_UNAVAILABLE", (r.stderr or "vm_stat 失败").strip()[:120])
    out = r.stdout
    page = 4096
    counts = {}
    for line in out.splitlines():
        if "page size of" in line:
            for tok in line.replace(".", " ").split():
                if tok.isdigit():
                    page = int(tok)
                    break
            continue
        name, _, val = line.partition(":")
        val = val.strip().rstrip(".")
        if val.isdigit():
            counts[name.strip()] = int(val)
    wanted = ("Pages free", "Pages inactive", "Pages speculative",
              "Pages purgeable")
    got = [counts[k] for k in wanted if k in counts]
    if not got:
        return Unknown("PARSE_FAILED", "vm_stat 输出里没有可用的页计数")
    return Ok(sum(got) * page // (1024 * 1024))
