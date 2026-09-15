"""C 层 · 模型聊天模板的**采证**（spec §2.3）：thinking 默认值与 reasoning_effort 值域。

模板是下载来的**不可信代码**：
  · 在**子进程**里渲染 —— 产品下限解释器没有 jinja2，而且渲染失控不能拖垮 CLI
  · `ImmutableSandboxedEnvironment` 子类，拦截 `*`（超大重复）与 `**`（超大指数）——
    构造超大字符串 / 整数是最便宜的资源耗尽，沙箱默认不管
  · 墙钟 5 s、输出 1 MB、RSS 1 GB 看门狗。**不用 setrlimit**：2026-09-15 实测
    macOS 上 RLIMIT_AS 与 RLIMIT_DATA 在 3.9.6 与 3.14 上都设置失败，只能在父进程轮询 `ps`
判不出一律 `Unknown`，调用方按 spec 处置（thinking 不加 flag、effort 视为空集）。
"""
import json
import os
import subprocess
import tempfile
import time

from .result import Ok, Unknown

WALL_SEC = 5.0
MAX_OUT_BYTES = 1 << 20
MAX_RSS_KB = 1 << 20                # ps 的 rss 单位是 KB ⇒ 1 GB
POLL_SEC = 0.05
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

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

def raise_exception(msg):
    raise jinja2.TemplateError(msg)

with open(sys.argv[1], encoding="utf-8") as fh:
    src = fh.read()
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
    for e in json.loads(sys.argv[2]):
        try:
            render(enable_thinking=True, reasoning_effort=e)
        except Exception:
            continue
        out["effort_domain"].append(e)
sys.stdout.write(json.dumps(out))
'''


def _rss_kb(pid, ps_path):
    # type: (int, str) -> object
    try:
        r = subprocess.run([ps_path, "-o", "rss=", "-p", str(pid)],   # noqa: S603
                           capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    txt = r.stdout.strip()
    return int(txt) if txt.isdigit() else None


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


def inspect_template(snapshot_path, python, ps_path="/bin/ps"):
    # type: (str, str, str) -> object
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
    fd, path = tempfile.mkstemp(prefix="mlxtpl.", suffix=".jinja")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src.value)
        return _run_renderer(python, path, ps_path)
    finally:
        os.unlink(path)


def _run_renderer(python, template_path, ps_path):
    # type: (str, str, str) -> object
    try:
        p = subprocess.Popen(                                      # noqa: S603
            [python, "-I", "-c", _RENDERER, template_path, json.dumps(list(EFFORT_ORDER))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    except OSError as exc:
        return Unknown("TOOL_UNAVAILABLE", str(exc)[:120])
    deadline = time.time() + WALL_SEC
    stop = None
    while p.poll() is None:
        if time.time() > deadline:
            stop = f"渲染超过 {WALL_SEC:.0f} 秒"
            break
        rss = _rss_kb(p.pid, ps_path)
        if rss is not None and rss > MAX_RSS_KB:
            stop = "渲染进程内存超过 1 GB"
            break
        time.sleep(POLL_SEC)
    if stop is not None:
        p.kill()
        p.communicate()
        return Unknown("TIMEOUT", stop)
    out, err = p.communicate()
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
