"""settings.json 与运行记录必须 0600（spec §3.4、§5.2）。每一种写入路径都查，
umask 固定为 022 —— 默认 umask 下 `open(tmp, "w")` 落成 0644。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PRE = """
import json, os, stat, tempfile
os.umask(0o022)
from mlx_server import store, supervisor
from mlx_server.ctx import Ctx
d = tempfile.mkdtemp()
ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None)
def mode(p):
    return oct(stat.S_IMODE(os.stat(p).st_mode))
"""


def test_settings_new_file_is_0600(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
store.commit(ctx, store.SETTINGS, store.SettingsTx({"fit.reserve_pct": 30}))
print(json.dumps({"mode": mode(store.settings_path(ctx))}))
""")
    assert got["mode"] == "0o600", f"新建 settings.json 权限 {got['mode']}"


def test_settings_overwrite_of_wide_file_is_0600(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
p = store.settings_path(ctx)
open(p, "w").write("{}")
os.chmod(p, 0o644)
store.commit(ctx, store.SETTINGS, store.SettingsTx({"fit.reserve_pct": 31}))
print(json.dumps({"mode": mode(p)}))
""")
    assert got["mode"] == "0o600", f"覆盖 0644 的 settings.json 后权限 {got['mode']}"


def test_run_record_is_0600(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
class S:
    service = "qwen"; model_arg = "qwen"; route = None
supervisor.write_record(ctx, S(), 111, 111, 8100, "t", "n0")
print(json.dumps({"mode": mode(os.path.join(d, "run", "qwen.json"))}))
""")
    assert got["mode"] == "0o600", f"运行记录权限 {got['mode']}"


def test_session_new_file_is_0600(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
from mlx_server import sessions
sid = sessions.new(ctx, "api")
print(json.dumps({"mode": mode(os.path.join(d, "sessions", sid + ".json"))}))
""")
    assert got["mode"] == "0o600", f"新建会话文件权限 {got['mode']}"


def test_session_save_overwrite_of_wide_file_is_0600(payload, sys_py):
    """`atomic.replace(p, mode=0o600)` 一次到位 —— 覆盖一个已存在的 0644 文件
    之后仍须是 0600，替换与 chmod 之间不得留一个宽权限窗口。"""
    got = run_floor(payload, sys_py, PRE + """
from mlx_server import sessions
sid = sessions.new(ctx, "api")
p = os.path.join(d, "sessions", sid + ".json")
os.chmod(p, 0o644)
sessions.save(ctx, sid, [{"role": "user", "content": "hi"}])
print(json.dumps({"mode": mode(p)}))
""")
    assert got["mode"] == "0o600", f"覆盖 0644 的会话文件后权限 {got['mode']}"


def test_history_path_new_file_is_0600(payload, sys_py):
    """`history_path` 首次创建 `.history` 须一次到位 0600。"""
    got = run_floor(payload, sys_py, PRE + """
from mlx_server import sessions
p = sessions.history_path(ctx)
print(json.dumps({"mode": mode(p)}))
""")
    assert got["mode"] == "0o600", f"新建 .history 权限 {got['mode']}"


def test_history_path_creates_without_separate_chmod(payload, sys_py):
    """`history_path` 须用一次系统调用把新文件创建为 0600（如
    `os.open(..., 0o600)`），不得先以默认 umask 创建再单独 `os.chmod` ——
    否则创建与 chmod 之间会留一个宽权限窗口。用会抛异常的假 `os.chmod`
    顶替真的，若实现仍调用它就会在此炸掉。"""
    got = run_floor(payload, sys_py, PRE + """
from mlx_server import sessions
def _boom(*a, **k):
    raise AssertionError("history_path 不应再调用 os.chmod")
os.chmod = _boom
p = sessions.history_path(ctx)
print(json.dumps({"mode": mode(p)}))
""")
    assert got["mode"] == "0o600", f"新建 .history 权限 {got['mode']}"
