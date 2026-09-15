"""`mlx up --dry-run` 经完整校验段七步（spec §2.1–§2.4、§3.2、§3.3）。真表、真注册表、真权重布局。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"
KEY = "K" * 43

UP = WORLD + """
from mlx_server import backendarg, runtime
from mlx_server.cmd.svc import Invocation, run
deps = {"backendarg_spec": backendarg.spec, "caps_drift": lambda: False}
def up(mid, dry=True):
    out = run(ctx, None, Invocation("up", mid, dry_run=dry), deps)
    p = getattr(out, "payload", None) or {}
    return {"rc": out.rc, "key": getattr(out, "msg_key", None),
            "argv": list(p.get("argv") or []), "env": p.get("env"),
            "route": p.get("route"), "sources": p.get("sources"),
            "timeout": p.get("ready_timeout_sec"), "dropped": p.get("dropped")}
VIS_CFG = fixture("qwen38/config.json")
VIS_TPL = fixture("qwen38/chat_template.jinja")
"""


def _run(payload, sys_py, body):
    return run_floor(payload, sys_py, UP + body, timeout=60,
                     FIXTURES=FIX, RENDER_PY=sys.executable, KEY=KEY)


def test_text_model_goes_to_mlx_lm(payload, sys_py):
    got = _run(payload, sys_py, """
seed("t", "org/t", 8101, config={})
print(json.dumps(up("t")))
""")
    assert got["rc"] == 0, got
    assert got["route"]["rule"] == "R4" and got["argv"][2] == "mlx_lm.server"
    assert "--decode-concurrency" in got["argv"] and got["timeout"] == 20


def test_vision_model_goes_to_mlx_vlm_with_key_masked(payload, sys_py):
    got = _run(payload, sys_py, """
seed("v", "org/v", 8102, config=VIS_CFG, template=VIS_TPL, machine={"size_mb": 28164.0})
put_caps(["mlx", "mlx_lm", "mlx_vlm"])
put_settings({"backend.api_key": "@KEY@"})
print(json.dumps(up("v")))
""")
    assert got["rc"] == 0, got
    assert got["route"]["rule"] == "R3" and got["argv"][2] == "mlx_vlm.server"
    assert got["argv"][-3:] == ["--max-num-seqs", "8", "--enable-thinking"]
    assert got["env"]["HF_HUB_OFFLINE"] == "1"
    assert got["env"]["MLX_VLM_SERVER_API_KEY"] == "****", "dry-run 泄露了后端密钥"
    assert KEY not in str(got), "dry-run 输出里出现密钥原值"
    assert got["route"]["effort_domain"] == ["low", "medium", "xhigh"]
    assert got["timeout"] == 20 + 3 * 28, "28164 MB ⇒ ceil(27.5) = 28 GB"


def test_refusals_and_precedence(payload, sys_py):
    got = _run(payload, sys_py, """
seed("nokey", "org/nokey", 8103, config=VIS_CFG, template=VIS_TPL)
seed("mtp", "org/mtp", 8104, config=fixture("qwen38-mtp/config.json"))
seed("withmtp", "org/withmtp", 8105, config={}, user={"draft_model": "mtp"})
r = {}
put_caps(["mlx", "mlx_lm"])
r["no_pkg_no_key"] = up("nokey")
put_caps(["mlx", "mlx_lm", "mlx_vlm"])
r["no_key"] = up("nokey")
r["mtp_alone"] = up("mtp")
r["mtp_as_draft"] = up("withmtp")
print(json.dumps(r))
""")
    assert (got["no_pkg_no_key"]["rc"], got["no_pkg_no_key"]["key"]) == (6, "svc.backend_missing")
    assert (got["no_key"]["rc"], got["no_key"]["key"]) == (6, "svc.backend_key_missing")
    assert (got["mtp_alone"]["rc"], got["mtp_alone"]["key"]) == (6, "svc.not_standalone_model")
    assert (got["mtp_as_draft"]["rc"], got["mtp_as_draft"]["key"]) == (6, "svc.draft_unsupported")


def test_effective_config_layers_reach_route_and_argv(payload, sys_py):
    """DF1 回归：per-model `modality` 与全局设置真的进 cfg（此前被硬编码 DEFAULTS 过滤掉）。"""
    got = _run(payload, sys_py, """
seed("forced", "org/forced", 8106, config=VIS_CFG, template=VIS_TPL, user={"modality": "text"})
put_settings({"max_concurrency": 2, "fit.reserve_pct": 30})
r = up("forced")
print(json.dumps(r))
""")
    assert got["rc"] == 0, got
    assert got["route"]["rule"] == "R4", "per-model modality=text 没生效"
    assert got["argv"][-4:] == ["--decode-concurrency", "2", "--prompt-concurrency", "2"]
    assert got["sources"]["fit.reserve_pct"] == "machine", "全局设置没进 cfg"


def test_wait_ready_timeout_is_forwarded_by_production_deps(payload, sys_py):
    got = _run(payload, sys_py, """
captured = {}
def fake_wait(c, spec, port, log_path=None, timeout_sec=20, poll_sec=0.25):
    captured["timeout"] = timeout_sec
    return None
runtime.wait_ready = fake_wait
runtime.build_deps(ctx)["wait_ready"](type("S", (), {"service": "x"})(), 8100, 107)
print(json.dumps(captured))
""")
    assert got == {"timeout": 107}, f"生产 deps 没把超时转发给 wait_ready：{got}"


def test_load_backend_notice_timeout_hint_and_run_record(payload, sys_py):
    """spec §3.3 / §3.4：真走执行段 —— 加载提示、超时说明「可能仍在加载」、运行记录快照字段。"""
    got = _run(payload, sys_py, """
seed("v", "org/v", 8107, config=VIS_CFG, template=VIS_TPL, machine={"size_mb": 2048.0})
put_caps(["mlx", "mlx_lm", "mlx_vlm"])
put_settings({"backend.api_key": "@KEY@"})
notes = []
deps.update({"spawn": lambda spec, cwd: {"pid": 999999, "pgid": 999999, "started_at": "t"},
             "wait_ready": lambda spec, port, timeout_sec: ("timeout", "tail"),
             "notice": notes.append})
out = run(ctx, None, Invocation("up", "v"), deps)
with open(os.path.join(DATA, "run", "v.json")) as fh:
    rec = json.load(fh)
print(json.dumps({"rc": out.rc, "key": out.msg_key, "detail": out.args.get("detail"),
                  "notes": notes, "record": rec}))
""")
    assert (got["rc"], got["key"]) == (4, "svc.ready_timeout"), got
    assert len(got["notes"]) == 1 and "26 秒" in got["notes"][0], "2048 MB ⇒ 20 + 3×2 = 26 秒"
    assert "还没加载完" in got["detail"], got["detail"]
    rec = got["record"]
    assert (rec["backend"], rec["backend_auth"], rec["max_concurrency"]) == ("mlx_vlm", True, 8)
    assert rec["effort_domain"] == ["low", "medium", "xhigh"] and len(rec["key_fp"]) == 12
    assert KEY not in json.dumps(rec), "运行记录里有密钥原值"
