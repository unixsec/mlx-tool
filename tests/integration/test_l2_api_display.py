"""spec §4.1「api info」、§6.5 status 网关行与 check 三项。读运行记录，不现场重算。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"
TOK = "T" * 43
KEY = "K" * 43

PROG = WORLD + r"""
import stat
from gateway import peers
from mlx_server import checks, route, supervisor, ui
from mlx_server.cmd import api as api_cmd, render_meta, render_svc  # noqa: F401
from mlx_server.cmd.svc import _tool_status
from mlx_server.ctx import Ctx
from mlx_server.result import Ok
from mlx_server.tables import TablesView
TV = TablesView(SHARE)            # 渲染文案键要显式给表（ui._text 不依赖装配顺序）
supervisor.running = lambda c, s, ps_path=None: Ok(True)
supervisor.port_owner = lambda c, s, lsof_path=None, ps_path=None: Ok(True)
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
nets = peers.NetView(lambda: (HW, IF), clock=lambda: 0.0, wall=lambda: 1700000000.0)
snap = nets.refresh()
put_settings({"api.token": "@TOK@", "backend.api_key": "@KEY@"})
os.makedirs(os.path.join(DATA, "run"), exist_ok=True)
def api_record(**kw):
    rec = {"pid": 4242, "pgid": 4242, "port": 8340, "started_at": "t", "model_arg": None,
           "type": "api", "schema_ver": 1, "nonce": "n", "bind": "0.0.0.0", "tls": False,
           "interpreter": "/usr/bin/python3", "interpreter_source": "system",
           "interpreter_version": "3.9.6", "tls_max": "TLSv1.2", "token_source": "config",
           "token_fp": route.key_fingerprint("@TOK@"),
           "backend_key_fp": route.key_fingerprint("@KEY@")}
    rec.update(snap); rec.update(kw)
    with open(os.path.join(DATA, "run", "api.json"), "w") as fh:
        fh.write(json.dumps(rec))
out = {}
api_record()
p = api_cmd.run(ctx, None, None, {}).payload
out["plain"] = {k: p.get(k) for k in ("base_url", "urls", "bind", "tls", "token",
                                      "plaintext_warning", "nets_source", "models")}
out["plain_text"] = ui.human(p, TV)
api_record(tls=True, interpreter="/venv/bin/python3", interpreter_source="venv", tls_max="TLSv1.3")
p = api_cmd.run(ctx, None, None, {}).payload
out["tls"] = [p["urls"][1]["openai"], p["plaintext_warning"], ui.human(p)]
api_record(nets_source="fallback", nets_error="networksetup rc=1")
out["check_fallback"] = checks.gateway_nets(ctx).value
api_record()
out["check_parsed"] = checks.gateway_nets(ctx).value
tool = _tool_status(ctx).payload
out["gw"] = {k: tool["gateway"].get(k) for k in ("bind", "tls", "allowed_count", "token_state")}
out["gw_text"] = ui.human(tool)
out["up_text"] = ui.human({"kind": "svc", "started": "api", "pid": 1, "port": 8340,
                           "bind": "0.0.0.0", "tls": False, "interpreter": "/usr/bin/python3"})
# 网关没在跑：即使运行记录里 token_source=env 且当前 shell 没设 MLX_GATEWAY_TOKEN
# （token_state 算出来是 env_unknown），也不该打印那句「无法比较是否已变更」——
# 进程都没在跑，提示轮换检测的局限没有意义（ruling 3）。
def _stopped(c, s, ps_path=None):
    return Ok(False) if s == "api" else Ok(True)
supervisor.running = _stopped
api_record(token_source="env")
tool_stopped = _tool_status(ctx).payload
out["gw_stopped"] = {k: tool_stopped["gateway"].get(k) for k in ("running", "token_state")}
out["gw_stopped_text"] = ui.human(tool_stopped)
supervisor.running = lambda c, s, ps_path=None: Ok(True)
api_record()
sp = os.path.join(DATA, "settings.json")
# Important 3：settings.json 损坏（非法 JSON）时，令牌行不能撒谎说
# 「未配置」——此前这里吞了一切异常，`token.configured` 塌成 False，
# 而网关其实带着令牌好好跑着；还把用户支去一条在同一份坏文件上会
# 一样读不出来的命令（`mlx bootstrap keys`）。
with open(sp, "w") as fh:
    fh.write("{not json")
p_corrupt = api_cmd.run(ctx, None, None, {}).payload
out["corrupt_settings"] = {"configured": p_corrupt["token"]["configured"],
                           "has_error": bool(p_corrupt.get("settings_error"))}
out["corrupt_settings_text"] = ui.human(p_corrupt, TV)
put_settings({"api.token": "@TOK@", "backend.api_key": "@KEY@"})
os.chmod(sp, 0o644); out["mode_wide"] = checks.settings_mode(ctx).value
os.chmod(sp, 0o600); out["mode_ok"] = checks.settings_mode(ctx).value
def stub(name, body):
    path = os.path.join(DATA, name)
    with open(path, "w") as fh:
        fh.write("#!/bin/bash\n" + body + "\n")
    os.chmod(path, 0o755)
    return path
def port_check(lsof_body, record=True, ps_body="echo 4242"):
    rec_path = os.path.join(DATA, "run", "api.json")
    if not record and os.path.exists(rec_path):
        os.remove(rec_path)
    c = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None,
            bins={"lsof": stub("lsof", lsof_body), "ps": stub("ps", ps_body),
                  "pgrep": "/usr/bin/pgrep", "vm_stat": "/usr/bin/vm_stat",
                  "networksetup": "/usr/sbin/networksetup", "ifconfig": "/sbin/ifconfig"},
            share_root=SHARE)
    r = checks.gateway_port(c)
    return ["U", r.kind] if r.is_unknown() else ["O", r.value]
import importlib
importlib.reload(supervisor)      # 撤掉上面对 running / port_owner 的替身，端口检查走真实判定
# Important 1：`gateway_port` 有记录时的分支（生产实际路径：`mlx check` 时网关在跑）——
# 此前两个用例都 record=False，这条分支从未被真的求值过。
out["port_foreign_with_record"] = port_check(
    "echo 9999", record=True,
    ps_body='if [ "$4" = "9999" ]; then echo 7777; else echo 4242; fi')
out["port_no_listener_with_record"] = port_check("exit 1", record=True)
out["port_probe_failed_with_record"] = port_check('echo boom 1>&2; exit 1', record=True)
out["port_free"] = port_check("exit 1", record=False)
out["port_foreign_no_record"] = port_check("echo 5555", record=False)
from mlx_server.cmd import check
out["items"] = list(check.ITEMS)
print(json.dumps(out))
"""


def test_api_info_status_and_checks(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, NET=NET, TOK=TOK, KEY=KEY)
    plain = got["plain"]
    labels = {(u["label"], u["openai"]) for u in plain["urls"]}
    assert labels == {("本机", "http://127.0.0.1:8340/v1"), ("本机", "http://[::1]:8340/v1"),
                      ("Wi-Fi", "http://172.20.10.3:8340/v1"),
                      ("Wi-Fi", "http://[2001:db8:0:1::104]:8340/v1"),
                      ("虚拟机共享网络", "http://10.211.55.2:8340/v1"),
                      ("虚拟机共享网络", "http://[fda8:69e7:f901:187b::10c]:8340/v1")}, labels
    assert plain["urls"][0]["label"] == "本机"
    assert {u["anthropic"] for u in plain["urls"] if u["label"] == "虚拟机共享网络"} == \
        {"http://10.211.55.2:8340", "http://[fda8:69e7:f901:187b::10c]:8340"}
    assert plain["base_url"] == "http://172.20.10.3:8340/v1", "主地址取第一个非回环 IPv4"
    assert (plain["bind"], plain["tls"], plain["plaintext_warning"]) == ("0.0.0.0", False, True)
    assert plain["token"] == {"configured": True, "source": "config", "state": "ok"}
    assert plain["nets_source"] == "parsed" and plain["models"] == []
    for s in ("Wi-Fi", "虚拟机共享网络", "utun4", "Thunderbolt Bridge", "TLS 未开启",
              "所有来源都要令牌", "TLS 关"):
        assert s in got["plain_text"], f"api info 缺「{s}」：\n{got['plain_text']}"
    assert TOK not in got["plain_text"] and KEY not in got["plain_text"]

    url, warn, text = got["tls"]
    assert url.startswith("https://") and warn is False and "TLSv1.3" in text
    assert "TLS 开" in text, f"tls=True 时 api info 该显示「TLS 开」：\n{text}"

    assert got["corrupt_settings"] == {"configured": None, "has_error": True}, got["corrupt_settings"]
    assert "读不出设置" in got["corrupt_settings_text"], got["corrupt_settings_text"]
    assert "未配置" not in got["corrupt_settings_text"], (
        f"settings.json 读不出时不该撒谎说「未配置」：\n{got['corrupt_settings_text']}")

    assert got["check_fallback"] is False and got["check_parsed"] is True
    assert got["gw"] == {"bind": "0.0.0.0", "tls": False, "allowed_count": 8, "token_state": "ok"}
    assert "绑定 0.0.0.0" in got["gw_text"] and "放行网段 8" in got["gw_text"]
    assert "✓ api 已启动 · 绑定 0.0.0.0 · TLS 关 · port 8340" in got["up_text"]
    assert got["gw_stopped"] == {"running": False, "token_state": "env_unknown"}, got["gw_stopped"]
    assert "令牌由环境变量设置" not in got["gw_stopped_text"], (
        f"网关没在跑时不该打印令牌轮换提示：\n{got['gw_stopped_text']}")
    assert "未运行" in got["gw_stopped_text"]
    assert got["mode_wide"] is False and got["mode_ok"] is True
    assert got["port_foreign_with_record"] == ["O", False], (
        "记录在但监听者不是本服务（pid、pgid 都对不上）⇒ 不通过")
    assert got["port_no_listener_with_record"] == ["O", True], (
        "记录在但此刻确知无人监听（进程静默退出，记录是陈旧的）⇒ 通过——"
        "这不是「外来监听者占用端口」要防的情形，是刻意的 Unknown 折叠")
    assert got["port_probe_failed_with_record"] == ["U", "PS_FAILED"], (
        "lsof 探测本身硬失败要穿透成 Unknown，不得塌成通过或失败")
    assert got["port_free"] == ["O", True]
    assert got["port_foreign_no_record"] == ["O", False], "网关没在跑而端口被占 ⇒ 不通过"
    assert got["items"][-3:] == ["gateway_nets", "gateway_port", "settings_mode"]
    assert len(got["items"]) == 12
