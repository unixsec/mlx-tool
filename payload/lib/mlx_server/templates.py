"""C 层 · 模型聊天模板的**采证**（spec §2.3）：thinking 默认值与 reasoning_effort 值域。

模板是下载来的**不可信代码**：
  · 在**子进程**里渲染 —— 产品下限解释器没有 jinja2，而且渲染失控不能拖垮 CLI
  · `ImmutableSandboxedEnvironment` 子类，拦截 `*`（超大重复）与 `**`（超大指数）——
    构造超大字符串 / 整数是最便宜的资源耗尽，沙箱默认不管
  · 墙钟 5 s、输出 1 MB、峰值内存 1 GB 看门狗。**不用 setrlimit**：2026-09-15 实测
    macOS 上 RLIMIT_AS 与 RLIMIT_DATA 在 3.9.6 与 3.14 上都设置失败；内存由子进程自己的线程
    读 `getrusage` 自查（此前父进程每 50 ms 起一个 `ps`，dry-run 也因此不是零外部命令）
  · **进程级沙箱**（1.2.0）：`sandbox-exec` 拒网络、拒写文件、拒读 home 与数据根（含 0600 的
    settings.json），只放行解释器自身的前缀。jinja2 沙箱一旦被绕过，模板也读不到密钥。
    模板经 stdin 传入，不落临时文件
判不出一律 `Unknown`，调用方按 spec 处置（thinking 不加 flag、effort 视为空集）。
"""
import json
import os
import subprocess

from .result import Ok, Unknown

WALL_SEC = 5.0
MAX_OUT_BYTES = 1 << 20
MAX_RSS_BYTES = 1 << 30
POLL_SEC = 0.05
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_CHILD_ENV = {"PATH": "/usr/bin:/bin"}  # Untrusted template code must not see parent secrets

_RENDERER = r'''
import json, sys
import jinja2
from jinja2.sandbox import ImmutableSandboxedEnvironment, SecurityError

class Bounded(ImmutableSandboxedEnvironment):
    intercepted_binops = frozenset({"*", "**"})

    def call_binop(self, context, operator, left, right):
        if operator == "**" and isinstance(right, int) and abs(right) > 64:
            raise SecurityError("exponent too large")
        if operator == "*":
            for seq, n in ((left, right), (right, left)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int) \
                        and len(seq) * n > 100000:
                    raise SecurityError("repeat too large")
        return super().call_binop(context, operator, left, right)

    def call(self, context, obj, *args, **kwargs):
        # 方法调用里的超大整数参数（"a".ljust(10**10)、center、zfill……）一次 C 调用就分配几十 GB，
        # 期间不释放 GIL，任何线程里的看门狗都来不及动作
        for v in list(args) + list(kwargs.values()):
            if isinstance(v, int) and not isinstance(v, bool) and abs(v) > 100000:
                raise SecurityError("argument too large")
        return super().call(context, obj, *args, **kwargs)

def raise_exception(msg):
    raise jinja2.TemplateError(msg)

src = sys.stdin.read()
env = Bounded(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
env.globals["raise_exception"] = raise_exception
env.globals["strftime_now"] = lambda fmt: ""
tmpl = env.from_string(src)
msgs = [{"role": "user", "content": "x"}]

def render(**kw):
    return tmpl.render(messages=msgs, add_generation_prompt=True, **kw)

base = render()
has_switch = "enable_thinking" in src
out = {"thinking_default": bool(has_switch and base == render(enable_thinking=True)),
       "effort_domain": []}
if "reasoning_effort" in src:
    for e in json.loads(sys.argv[1]):
        try:
            render(enable_thinking=True, reasoning_effort=e)
        except Exception:
            continue
        out["effort_domain"].append(e)
sys.stdout.write(json.dumps(out))
'''
# 渲染器包一层：异常只写截断后的类型与消息（模板可以 raise 一个巨大的字符串）
_RENDERER = ("import sys\ntry:\n" + "".join("    " + ln + "\n" for ln in _RENDERER.strip("\n").split("\n"))
             + "except BaseException as _e:\n"
             + "    sys.stderr.write((type(_e).__name__ + ': ' + str(_e))[:2000])\n"
             + "    sys.exit(1)\n")


_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _resident_bytes(pid):
    # type: (int) -> object
    """子进程的常驻内存（字节），经 libproc 的 `proc_pidinfo(PROC_PIDTASKINFO)`，**不起子进程**。
    取不到 ⇒ None（此时只剩墙钟兜底）。"""
    import ctypes
    try:
        lib = _resident_bytes.lib
    except AttributeError:
        try:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        except OSError:
            lib = None
        _resident_bytes.lib = lib
    if lib is None:
        return None
    buf = (ctypes.c_uint64 * 12)()                  # struct proc_taskinfo：96 字节
    n = lib.proc_pidinfo(int(pid), 4, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
    return int(buf[1]) if n == ctypes.sizeof(buf) else None
_PREFIXES = {}                     # 解释器路径 → 它的 sys.prefix / base_prefix（本进程内缓存）


def _prefixes(python):
    # type: (str) -> list
    """解释器自己的前缀（venv 与它的基础安装）。沙箱只放行读这些 —— 解释器本身可信，问它一次。"""
    if python not in _PREFIXES:
        try:
            r = subprocess.run([python, "-I", "-c",                     # noqa: S603
                                "import json,sys;print(json.dumps([sys.prefix,sys.base_prefix]))"],
                               capture_output=True, text=True, timeout=5, check=False,
                               env=dict(_CHILD_ENV), stdin=subprocess.DEVNULL)
            got = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            got = []
        root = os.path.dirname(os.path.dirname(os.path.abspath(python)))
        out = []
        for d in [root] + list(got):
            for x in (d, os.path.realpath(d)):
                if x and x not in out:
                    out.append(x)
        _PREFIXES[python] = out
    return _PREFIXES[python]


def _sb_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(python, deny_read=()):
    # type: (str, tuple) -> str
    """渲染子进程的 sandbox-exec 规则。后写的规则优先：先拒、再放行解释器前缀。"""
    home = os.path.expanduser("~")
    lines = ["(version 1)", "(allow default)", "(deny network*)",
             "(deny file-write*)", '(allow file-write* (literal "/dev/null"))']
    for d in [home] + [x for x in deny_read if x]:
        lines.append(f"(deny file-read-data (subpath {_sb_str(os.path.realpath(d))}))")
        if os.path.realpath(d) != d:
            lines.append(f"(deny file-read-data (subpath {_sb_str(d)}))")
    for d in _prefixes(python):
        lines.append(f"(allow file-read-data (subpath {_sb_str(d)}))")
    return "\n".join(lines) + "\n"


def _template_source(snapshot_path):
    # type: (str) -> object
    """模板文本：`chat_template.jinja` 优先，其次 `tokenizer_config.json` 的字符串模板。"""
    p = os.path.join(snapshot_path, "chat_template.jinja")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return Ok(fh.read())
        except OSError as exc:
            return Unknown("PARSE_FAILED", f"{p}：{exc}")
    tc = os.path.join(snapshot_path, "tokenizer_config.json")
    try:
        with open(tc, encoding="utf-8") as fh:
            tpl = json.loads(fh.read()).get("chat_template")
    except (OSError, ValueError, AttributeError):
        tpl = None
    if isinstance(tpl, str) and tpl:
        return Ok(tpl)
    return Unknown("PARSE_FAILED", "没有 chat_template.jinja，tokenizer_config.json 里也没有字符串模板")


def inspect_template(snapshot_path, python, ps_path="/bin/ps", deny_read=()):
    # type: (str, str, str, tuple) -> object
    """→ `Ok({"thinking_default": bool, "effort_domain": [...]})` 或 `Unknown`。

    `thinking_default`：渲染「未传 enable_thinking」与「enable_thinking=True」逐字节相同 ⇒ True。
    `effort_domain`：以 enable_thinking=True 逐个试 `EFFORT_ORDER`，不抛异常的组成值域
    （qwen38 实测 low / medium / xhigh；客户端常发的 high 会让模板 raise）。
    """
    if not python or not os.path.exists(python):
        return Unknown("TOOL_UNAVAILABLE", "没有可用的 venv 解释器（模板渲染需要 jinja2）")
    src = _template_source(snapshot_path)
    if src.is_unknown():
        return src
    return _run_renderer(python, src.value, deny_read)


def _run_renderer(python, source, deny_read=()):
    # type: (str, str, tuple) -> object
    argv = [python, "-I", "-c", _RENDERER, json.dumps(list(EFFORT_ORDER))]
    if os.access(_SANDBOX_EXEC, os.X_OK):
        argv = [_SANDBOX_EXEC, "-p", sandbox_profile(python, deny_read)] + argv
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # noqa: S603
                             stdin=subprocess.PIPE, env=dict(_CHILD_ENV))
    except OSError as exc:
        return Unknown("TOOL_UNAVAILABLE", str(exc)[:120])
    import threading
    import time
    box = {}

    def _talk():
        try:
            box["out"] = p.communicate(source.encode("utf-8"))
        except (OSError, ValueError) as exc:
            box["exc"] = exc
    th = threading.Thread(target=_talk, daemon=True)
    th.start()
    # **内存看门狗在父进程**：子进程里的线程要拿 GIL，一次巨大的 C 级分配期间它动不了
    deadline, stop = time.monotonic() + WALL_SEC, None
    while th.is_alive():
        if time.monotonic() > deadline:
            stop = f"渲染超过 {WALL_SEC:.0f} 秒"
            break
        rss = _resident_bytes(p.pid)
        if rss is not None and rss > MAX_RSS_BYTES:
            stop = "渲染进程内存超过 1 GB"
            break
        th.join(POLL_SEC)
    if stop is not None:
        p.kill()
        th.join(5)
        return Unknown("TIMEOUT", stop)
    if "exc" in box:
        return Unknown("TOOL_UNAVAILABLE", str(box["exc"])[:120])
    out, err = box["out"]
    if p.returncode != 0:
        return Unknown("PARSE_FAILED", (err or b"").decode("utf-8", "replace").strip()[-200:])
    if len(out) > MAX_OUT_BYTES:
        return Unknown("PARSE_FAILED", "渲染器输出超过 1 MB")
    try:
        obj = json.loads(out.decode("utf-8"))
    except ValueError:
        return Unknown("PARSE_FAILED", "渲染器输出不是 JSON")
    got = set(obj.get("effort_domain") or [])
    return Ok({"thinking_default": bool(obj.get("thinking_default")),
               "effort_domain": [e for e in EFFORT_ORDER if e in got]})
