"""B 层 · **生产环境的依赖实现**。

根因（本模块存在的理由）：D 层的每个命令都通过 `deps` 取外部能力
（spawn / fetch / fingerprint / transport / confirm），设计上这是对的 ——
它让 L2 能用桩驱动。但**从没写过生产实现**，于是 `entry` 无处可取，
每条真实命令都撞在 `Refused(9, "*.no_*")` 上。

本模块是那些注入点的**唯一生产实现处**。测试仍然注自己的桩，
两者互不影响 —— 这正是注入点设计要的。
"""
import collections
import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import checks, probe, proc

# **三包清单的唯一定义在 C 层 `caps`**。此前这里另写一份，与
# `cmd/caps.py:PACKAGES` 靠 dict key 的字面相等耦合、零守卫 ——
# `fingerprint` 按这份生成那串 JSON，`caps` 按另一份去筛它的 key，
# 任一处改了名字，筛出来的就是空集而没有任何东西会红。
from .caps import PACKAGES as VENV_PACKAGES
from .result import Ok, Unknown, is_unknown
from .weights import _dir_bytes

# ── spawn ──────────────────────────────────────────────────────────

def spawn(spec, cwd, log_path=None, venv_python=None, code_root=None):
    # type: (object, str, str, str) -> dict
    """起后端进程。**新会话新进程组**，日志重定向由本函数做（`BCR-R-24`）。

    返回 `{"pid", "pgid", "started_at"}`；起不来返回 `None`。
    `started_at` 取自 `ps`，**不是本地时钟** —— 它要和 `proc.owns` 的比较基准同源。
    """
    argv = list(spec.argv)
    if venv_python and argv and argv[0] in VENV_PACKAGES:
        argv[0] = venv_python                      # `mlx_lm` 这类名字换成 venv 解释器

    env = dict(os.environ, **dict(spec.env or {}))
    if code_root:
        # 网关以 `-m gateway` 起，包在代码根下；T1 在 code_root/lib
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (code_root, os.path.join(code_root, "lib"),
                         env.get("PYTHONPATH", "")) if p])

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115

    # ── 屏蔽窗口（`G-RES-09(b)` 的三个调用点之一）─────────────────
    # 覆盖「spawn → `proc.started_at` → 返回」这一段。它关闭的是 E-7：
    # 180 秒就绪等待里按 Ctrl-C，shell 退出而后台 python 继续把 28 GB 读进
    # 内存 —— 因为 spawn 与「把 pid 记下来」之间被信号截断，
    # **没有人再知道该去杀谁**。
    # 窗口内**唯一**的可阻塞调用是 `proc.started_at_raw` 那一次 `ps`
    # （窗口子表登记的集合，`G-RES-09(c)` 的相等断言）——
    # 它有超时上界，符合共同判据 J-2「窗口内不得存在没有上界的停留」。
    try:
        with proc.masked():
            try:
                p = subprocess.Popen(                       # noqa: S603
                    argv, cwd=cwd,
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                    start_new_session=True,             # 新会话新进程组 ⇒ killpg 覆盖得到
                    # **子进程侧恢复默认掩码**（BC-C-05）。exec 保留掩码、
                    # 只重置处理器 —— 漏了这一步后端会永久屏蔽 INT/TERM，
                    # `down` 的 TERM 阶梯失效，所有后端都杀不掉。
                    preexec_fn=proc.reset_child_mask,   # noqa: PLW1509
                    env=env,
                )
            except (OSError, ValueError):
                return None

            st = proc.started_at_raw(p.pid)
            if is_unknown(st):
                # 起了但读不到启动时间 ⇒ 运行记录会缺比较基准 ⇒ 收掉它，
                # **在窗口内**收 —— 出了窗口再被信号打断就又是一个孤儿
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                return None
            return {"pid": p.pid, "pgid": os.getpgid(p.pid), "started_at": st.value}
    finally:
        if log_path and log is not subprocess.DEVNULL:
            log.close()


# ── fetch（权重下载）────────────────────────────────────────────────

# 自算进度的间隔。**不能太短**：每次 tick 要 `os.walk` 整个目录，
# 27 GB 的仓库有上百个分片，扫一次不是免费的。
_TICK_SECONDS = 5.0


def fetch(repo, revision, dest_dir, hf_bin=None, on_line=None, on_tick=None):
    # type: (str, str, str, str, object, object) -> dict
    """用 `hf` CLI 下载。**venv/bin 必须在 PATH 上** —— v6 死在裸调 `hf` 上。

    **进度直通**（`on_line`）：`hf` 的进度条走 stderr，此前这里用
    `capture_output=True` 把它整个吞掉 —— 用户等一个 30 GB 的下载，
    屏幕上半小时什么都没有，分不清「在下」还是「卡住了」。
    现在逐行读 stderr：一边转交给 `on_line`（走 ui 的过程流，即 stderr），
    一边留最后若干行做失败原因 —— 两个需求本来就不冲突，
    是 `capture_output` 这个写法把它们变成了二选一。

    **但只靠 `on_line` 是不够的**〔2026-09-02 实测〕：为了逐行读，
    `stderr` 必须是管道；而 `hf`（tqdm）检测到 stderr 不是 tty 就**整个禁用
    进度条**。实测 27.5 GB 的下载落盘 681 MB 时，日志里只有两行开场白 ——
    「进度直通」直通的是一条空管道。**声明做了进度 ≠ 用户看得到进度。**

    `on_tick` 是不依赖 `hf` 输出的第二条路：每 `_TICK_SECONDS` 秒被调一次
    （靠 `select` 超时，不用线程 —— 线程会碰 INV-07 的信号屏蔽面），
    由调用方去扫目录算「已下多少、多快」。管道、后台、重定向到文件，
    三种情形下它都照常工作，因为它不问 `hf` 任何事。
    """
    exe = hf_bin or "hf"
    argv = [exe, "download", repo, "--local-dir", dest_dir]
    if revision:
        argv += ["--revision", revision]
    try:
        proc_h = subprocess.Popen(                       # noqa: S603
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "reason": f"tool_unavailable: {e}"}

    tail = collections.deque(maxlen=12)      # 失败时报最后几行，不是整篇日志
    try:
        while True:
            # **`select` 而不是 `for raw in stderr`**：后者在 `hf` 沉默时
            # 会一直阻塞，tick 永远没机会跑 —— 而 `hf` 恰恰在管道模式下沉默。
            ready = select.select([proc_h.stderr], [], [], _TICK_SECONDS)[0]
            if not ready:
                if on_tick is not None:
                    on_tick(dest_dir)
                continue
            raw = proc_h.stderr.readline()
            if not raw:                      # EOF ⇒ 子进程关了 stderr
                break
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            tail.append(line)
            if on_line is not None:
                on_line(line)
        rc = proc_h.wait(timeout=7200)
    except subprocess.TimeoutExpired:
        proc_h.kill()
        return {"ok": False, "reason": "下载超时（2 小时）"}
    except KeyboardInterrupt:
        # Ctrl-C：**先收掉子进程**再向上抛，否则 hf 继续在后台占着带宽下载
        proc_h.kill()
        proc_h.wait()
        raise
    finally:
        with contextlib.suppress(Exception):
            proc_h.stderr.close()

    if rc != 0:
        return {"ok": False, "reason": "\n".join(tail)[-300:]}
    return {"ok": True, "path": dest_dir, "revision": revision or "main"}


# ── fingerprint（venv 三包指纹）────────────────────────────────────

def fingerprint(venv_python=None):
    # type: (str) -> object
    """venv 里三个包的版本指纹。**三态** —— 探不到不得当成「没变」。"""
    py = venv_python or sys.executable
    code = (
        "import json\n"
        "out={}\n"
        f"for m in {list(VENV_PACKAGES)!r}:\n"
        "    try:\n"
        "        import importlib.metadata as md; out[m]=md.version(m)\n"
        "    except Exception: out[m]=None\n"
        "print(json.dumps(out,sort_keys=True))\n"
    )
    try:
        r = subprocess.run([py, "-c", code], capture_output=True,  # noqa: S603
                           text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return Unknown("TOOL_UNAVAILABLE", str(e))
    if r.returncode != 0:
        return Unknown("PARSE_FAILED", r.stderr.strip()[:200])
    try:
        vers = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Unknown("PARSE_FAILED", r.stdout[:120])
    if all(v is None for v in vers.values()):
        return Unknown("TOOL_UNAVAILABLE", "三个包一个都没装")
    return Ok(json.dumps(vers, sort_keys=True))


# ── transport（chat / bench 用）────────────────────────────────────

def send_chat(port, messages, model=None, timeout_sec=300, host="127.0.0.1"):
    # type: (int, list, str, int, str) -> object
    """向本机后端发一次 chat 请求。失败返回 `None`（调用方构造 `Refused(3)`）。"""
    # **`model` 必须是后端认得的那个名字**（INV-01 态 A：短 id）。
    # 此前默认 `"local"`：mlx_lm 把它当成 HuggingFace 仓库名去网上查，
    # 401 之后返回错误体 ⇒ `body["choices"]` KeyError ⇒ 恒 None ⇒
    # `chat` / `bench` 在服务明明在跑时报「连不上后端」。
    payload = {"model": model or "local", "messages": messages, "stream": False}
    req = urllib.request.Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _strip_chat_markers(text)


# 后端有时把 chat 模板的结束标记一起吐出来，用户每条回复末尾都会看到
# `<|im_end|>` 这样的东西。**只裁末尾的已知标记**，不做正则清洗 ——
# 模型正文里出现这些字面量是合法的，全局替换会改坏内容。
_END_MARKERS = ("<|im_end|>", "<|endoftext|>", "<|eot_id|>", "</s>", "<end_of_turn>")


def _strip_chat_markers(text):
    # type: (object) -> object
    if not isinstance(text, str):
        return text
    out = text.rstrip()
    changed = True
    while changed:
        changed = False
        for m in _END_MARKERS:
            if out.endswith(m):
                out, changed = out[: -len(m)].rstrip(), True
    return out


def bench_once(port, prompt, host="127.0.0.1", model=None):
    # type: (int, str, str, str) -> dict
    """一次基准：首 token 延迟 + 总时长。`model` 透传给后端（见 `send_chat`）。"""
    t0 = time.time()
    reply = send_chat(port, [{"role": "user", "content": prompt}],
                      model=model, host=host)
    if reply is None:
        return None
    dt = time.time() - t0
    return {"elapsed_sec": round(dt, 3), "chars": len(reply),
            "chars_per_sec": round(len(reply) / dt, 1) if dt > 0 else None}


# ── confirm（破坏性确认）──────────────────────────────────────────

def confirm(expect, prompt=None):
    # type: (str, str) -> bool
    """只从 `/dev/tty` 读；**EOF ⇒ 拒绝**（`02` §1 的 ui 原语明写）；三种绕过全拒。

    **读写必须用两个独立的流。** `open("/dev/tty", "r+")` 打得开，但在同一个
    buffered 流上 write 之后 readline 需要 seek，而 tty 不可 seek ⇒
    抛 `io.UnsupportedOperation`。它继承自 `OSError`，于是被下面的
    `except OSError` 吞掉、恒返回 False ⇒ **确认永远不通过、`mlx model rm`
    完全不可用**，而且提示串一次都没打印出来。实测踩过。
    """
    try:
        w = open("/dev/tty", "w")                       # noqa: SIM115
    except OSError:
        return False                # 无 tty ⇒ 拒绝，不回落 stdin
    try:
        r = open("/dev/tty")                       # noqa: SIM115
    except OSError:
        w.close()
        return False
    try:
        if prompt:
            w.write(prompt.rstrip("\n") + "\n")
        w.write(f"键入 {expect} 确认: ")
        w.flush()
        line = r.readline()
    except OSError:
        return False
    finally:
        w.close()
        r.close()
    return bool(line) and line.strip() == expect


# ── 装配 ───────────────────────────────────────────────────────────

def build_deps(ctx):
    # type: (object) -> dict
    """**生产 deps 的唯一装配点**。测试不用它，测试注自己的桩。"""
    venv_py = os.path.join(getattr(ctx, "venv", ""), "bin", "python3")
    venv_py = venv_py if os.path.exists(venv_py) else None
    venv_bin = os.path.join(getattr(ctx, "venv", ""), "bin")
    hf = os.path.join(venv_bin, "hf")
    return {
        "spawn": lambda spec, cwd: spawn(
            spec, cwd,
            log_path=os.path.join(ctx.root_data, "logs", f"{spec.service}.log"),
            venv_python=venv_py, code_root=ctx.root_code),
        # 进度走 **stderr**（过程流，INV-17）—— stdout 留给主产物，
        # `mlx model install ... --json | jq` 不会被进度条弄脏。
        "fetch": lambda repo, rev, dest: fetch(
            repo, rev, dest, hf_bin=hf if os.path.exists(hf) else None,
            on_line=_make_progress(), on_tick=_make_ticker(_repo_bytes(repo))),
        "announce": _announce,
        "fingerprint": lambda: fingerprint(venv_py),
        "send": send_chat,
        "bench": bench_once,
        "confirm": confirm,
        "caps_drift": lambda: _drift(ctx, venv_py),
        "checks": checks.ALL,          # 九项健康检查的生产实现
        "wait_ready": lambda spec, port, timeout_sec: wait_ready(
            ctx, spec, port, timeout_sec=timeout_sec,
            log_path=os.path.join(ctx.root_data, "logs", f"{spec.service}.log")),
        # 过程说明（spec §3.3 的加载提示）走 stderr（INV-17）
        "notice": lambda text: (sys.stderr.write(text + "\n"), sys.stderr.flush()),
        # `bootstrap verify` 要按分发清单核对产物 —— 没有它就恒返回
        # 「读不到 share/ 下的表」，而表明明在（实测过）
        "tables": _tables(ctx),
        "nonce": lambda: f"{int(time.time() * 1000):x}",
        # ── 以下三个此前**没有生产者** —— 消费点写着 `deps.get(K)`，
        #    `.get()` 的 None 让整个校验分支静静跳过。守卫 `test_d6` 现在盯着它们。
        # `ports.conflict_check` 的契约逐字是「在其所属命令的真实运行模式下
        # **必然执行**（不得因降级模式恒空跑）」，而它恰恰恒空跑了。
        "ports_conflict": lambda target: _ports_conflict(ctx),
        # 模型类型的值域，**来自 `types.tsv` 的主键列**（设计：「type ∈ types.tsv
        # 主键集合」）。此前它是 `schema.MODEL_TYPES` 常量 —— 两道域校验
        # （cmd 与 store）读同一个常量，少一处引用就一起失效。
        # **惰性读**：装配期直接读表会让「表缺失」变成**每一条命令**都崩，
        # 连 `help` / `version` 都起不来 —— 而它们的契约是「任何状态下都能跑」。
        # 现在只有真要用到类型域的那条命令会碰它。
        "types": lambda: tuple(r["type"] for r in _tables(ctx).model_types()),
        # type 四级判定链的第 ④ 级。缺了它，不给 `--type` 必然 rc 9。
        "ask": _ask,
        # `chat` 的多轮读取源与输出。**提示走 stderr、回复走 stdout** ——
        # 台账给 chat 的主产物是「逐字节只含回复正文」，把「你 > 」混进去
        # 会让 `mlx chat x > out.txt` 的文件里夹着提示符。
        "read_line": _read_line,
        "emit": _emit,
        # 容量判档：**探针值由调用方注入**（`fitness` 是纯函数，不调探针）。
        "avail_mem_mb": probe.available_mem_mb,
        "rss_mb_of": probe.rss_kb,
    }


def _announce(repo, already_bytes):
    # type: (str, int) -> None
    """下载开始前的一行：下什么、到哪、已有多少。走 stderr（过程流）。"""
    tip = f"  ↓ 下载 {repo}"
    if already_bytes > 0:
        # 单位随大小走：断点续传时已有 30 MB 报成「0.0 GB」，
        # 看起来像「什么都没下」—— 而那正是这行想否定的印象
        n, unit = ((already_bytes / 1e9, "GB") if already_bytes >= 1e9
                   else (already_bytes / 1e6, "MB"))
        tip += f"（已有 {n:.1f} {unit}，断点续传）"
    sys.stderr.write(tip + "\n")
    sys.stderr.flush()


# `hf` 的 stderr 里混着与下载**无关**的运行时告警（urllib3/LibreSSL 那条
# 在每次调用都出现）。它们不是进度，混在进度里只会让人以为出了问题 ——
# 而真正的失败原因仍然进 `tail`，不会因为这里过滤而丢。
# **纯噪音**：丢掉不损失任何信息。
# `hf` 的这两条对本工具的用户零价值 —— 「没设 HF_TOKEN」是常态（公开仓库不需要），
# 「hf-cli skill 没装」是给别的 agent 看的广告。
_NOISE = ("NotOpenSSLWarning", "urllib3 v2 only supports", "warnings.warn(",
          "You are sending unauthenticated requests",
          "Hint: The `hf-cli` skill is not installed")

# **要折叠但不能丢的**：`hf` 抢锁时每 0.4 秒刷一行，正常情况下刷 5 行就过去了
# （2026-09-02 实测），但**真卡住时它是唯一的线索**。无条件过滤掉，用户面对的
# 就是一个不动的屏幕，且没有任何东西告诉他在等什么。
# 折叠成「同一条 ×N」两头都保住：不刷屏，卡住时计数还在涨。
_COLLAPSE = ("Still waiting to acquire lock",)


def _make_progress():
    # type: () -> object
    """造一个带状态的进度回调。**状态是折叠计数所必需的** ——
    无状态版本要么刷屏要么丢信息，两者都不可接受。"""
    state = {"last": None, "n": 0}

    def emit(text):
        sys.stderr.write(text + "\n")
        sys.stderr.flush()

    def flush_pending():
        if state["n"] > 1:
            emit(f"   （上一条重复 {state['n']} 次）")
        state["last"], state["n"] = None, 0

    def progress(line):
        # type: (str) -> None
        """下载进度：**逐行写 stderr 并立刻 flush**。

        不 flush 的话管道缓冲会把 30 GB 下载的全部进度攒到最后一起吐出来 ——
        那与完全没有进度是一回事（用户要的是「现在到哪了」，不是事后的日志）。
        """
        if any(n in line for n in _NOISE):
            return
        key = next((c for c in _COLLAPSE if c in line), None)
        if key is not None:
            if state["last"] == key:
                state["n"] += 1
                return                 # 攒着，等它结束或换行再一次性说
            flush_pending()
            state["last"], state["n"] = key, 1
            emit(line)
            return
        flush_pending()                # 换了内容 ⇒ 先把攒的计数吐出来
        emit(line)

    return progress


def _repo_bytes(repo, timeout=6.0):
    # type: (str, float) -> int
    """仓库总字节数，**取不到就 None**（不是 0 —— 0 会让百分比恒为 inf）。

    只用来给进度加百分比与 ETA。**任何失败都必须静默降级**：
    离线、限流、API 改字段、私有仓库 401 —— 每一种都不该妨碍下载本身。
    为一个装饰性的百分比让 27 GB 的下载失败，是拿主功能赌增强项。
    """
    url = f"https://huggingface.co/api/models/{repo}?blobs=true"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as fh:   # noqa: S310
            data = json.loads(fh.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    total = 0
    for f in data.get("siblings") or []:
        if isinstance(f, dict) and isinstance(f.get("size"), int):
            total += f["size"]
    return total or None


def _fmt_bytes(n):
    # type: (float) -> str
    """按量级选单位。27 GB 报成 `27648.0 MB` 是能看，但看不快。"""
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{int(n)} B"


def _make_ticker(total_bytes=None):
    # type: (int) -> object
    """造一个「已下多少、多快、还要多久」的心跳回调。

    **总量可以是 None** —— 拿不到就只报已下载量与速率，那已经足够回答
    用户真正在问的问题（「它还活着吗」）。为了报百分比去卡一个可能失败的
    网络请求，是把主功能押在增强项上。
    """
    state = {"n": 0, "t": None, "start": None, "start_n": None}

    def tick(dest_dir):
        # type: (str) -> None
        now = time.time()
        n = _dir_bytes(dest_dir)
        if state["t"] is None:                  # 第一拍只取基线：没有前值就没有速率
            state.update(n=n, t=now, start=now, start_n=n)
            return
        dt, dn = now - state["t"], n - state["n"]
        state.update(n=n, t=now)
        if dn <= 0:
            # **不动也要报**。「卡住了」是用户最需要知道的状态，
            # 而恰恰是这种时候没有任何新字节可报 —— 沉默会被读成「在下」。
            sys.stderr.write(f"   … 已下 {_fmt_bytes(n)}（本轮无新增，仍在等）\n")
            sys.stderr.flush()
            return
        rate = dn / dt if dt > 0 else 0
        msg = f"   … 已下 {_fmt_bytes(n)}  {_fmt_bytes(rate)}/s"
        if total_bytes:
            pct = min(100.0, n * 100.0 / total_bytes)
            # 剩余时间用**整段平均速率**算，不用本轮瞬时值 —— 后者抖得厉害，
            # 报出来的 ETA 会在几秒和几十分钟之间跳，反而不可信。
            avg = (n - state["start_n"]) / max(1e-6, now - state["start"])
            eta = (total_bytes - n) / avg if avg > 0 else 0
            msg += f"  {pct:.1f}%  约剩 {int(eta // 60)} 分"
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    return tick


def _read_line(prompt):
    # type: (str) -> object
    """读一行。有 tty 就用 tty，否则读 stdin（管道也要能喂）。EOF ⇒ `None`。"""
    if sys.stdin.isatty():
        sys.stderr.write(prompt)
        sys.stderr.flush()
    line = sys.stdin.readline()
    return None if line == "" else line.rstrip("\n")


def _emit(text):
    # type: (str) -> None
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _ports_conflict(ctx):
    # type: (object) -> object
    """网关端口 × 全部模型端口互斥。**原样返回 `conflict_check` 的结果**
    （`None` 或带 `detail` 的 `Refused(7)`）—— 塌成 bool 会丢掉「谁跟谁撞」，
    用户拿到的就是一句没有参数的「端口冲突：」。"""
    from . import ports, store
    try:
        recs = store.load(ctx, store.MODELS).records
    except Exception:                              # noqa: BLE001
        return None                                # 读不到注册表不是端口冲突
    return ports.conflict_check(ctx, recs)


def _ask(msg_key):
    # type: (str) -> str
    """向用户问一句并读一行。**只从 `/dev/tty` 读**，无 tty ⇒ 返回空串。

    读写用两个独立的流 —— 同一个 buffered 流上 write 之后 readline 需要 seek，
    而 tty 不可 seek，抛的 `io.UnsupportedOperation` 继承自 `OSError`，
    会被吞掉（`confirm` 踩过这个坑，见它的 docstring）。
    """
    try:
        w = open("/dev/tty", "w")                  # noqa: SIM115
    except OSError:
        return ""
    try:
        r = open("/dev/tty")                       # noqa: SIM115
    except OSError:
        w.close()
        return ""
    try:
        w.write(f"{msg_key}: ")
        w.flush()
        return (r.readline() or "").strip()
    except OSError:
        return ""
    finally:
        r.close()
        w.close()


def wait_ready(ctx, spec, port, log_path=None, timeout_sec=20, poll_sec=0.25):
    # type: (object, object, int, str, float, float) -> object
    """等待服务就绪。→ `None` 就绪 · `("dead", 日志尾)` 已退出 · `("timeout", 日志尾)`。

    **先看进程是否已退出，再探端口**（`02` §7.4 明写顺序不可反）：
    反过来会把「三秒就崩」的进程等满整个超时，还报不出真正的原因。

    没有这一步时，`mlx up api` 在端口被占的情况下 **rc=0 且报 started**，
    而进程早已因 `Address already in use` 退出 —— 报成功但没产生效果。
    """
    import time

    from . import supervisor
    from .result import is_unknown

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        alive = supervisor.running(ctx, spec.service)
        if not is_unknown(alive) and alive.value is False:
            return ("dead", _log_tail(log_path))
        # **判据是 `port_owner` 不是 `listener_pid`**：后者只回答「端口上有没有人」，
        # 而端口被**别的进程**占住时它同样为真 ⇒ 会把「我们没起来、别人占着」
        # 误判成就绪，于是 rc=0 报 started 而进程已死（实测踩过）。
        # `port_owner` 回答的是「监听者是不是我们记录的那个 pid/pgid」。
        owner = supervisor.port_owner(ctx, spec.service)
        if not is_unknown(owner) and owner.value is True:
            return None                      # 确认是我们在监听 ⇒ 就绪
        time.sleep(poll_sec)
    return ("timeout", _log_tail(log_path))


def _log_tail(path, lines=12):
    # type: (str, int) -> str
    """日志尾部。**失败结论必须带上它** —— 否则用户只知道失败，不知道为什么。"""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as fh:
            tail = fh.read()[-4000:].decode("utf-8", "replace").splitlines()
        return "\n".join(tail[-lines:])
    except OSError:
        return ""


def _tables(ctx):
    from .tables import open_tables
    return open_tables(ctx)          # 表根的唯一来源是 `ctx.share_root`


def _drift(ctx, venv_py):
    # `caps` 是 C 层（顶部已 import）。此前这里 `from .cmd import caps` ——
    # B 层反向 import 一个 D 层编排器，五条层序反向边里的一条。
    from . import caps as _caps
    return _caps.drift(ctx, lambda: fingerprint(venv_py))
