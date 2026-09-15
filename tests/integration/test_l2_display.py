"""spec §6.5 显示。每条都从**真实**注册表、权重布局与运行记录走到载荷与人类可读文本。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"

PROG = WORLD + """
from mlx_server import runtime, ui
from mlx_server.cmd import config as config_cmd, model_query
from mlx_server.cmd import render_config, render_model, render_svc  # noqa: F401  登记渲染器
from mlx_server.cmd.svc import Invocation, _tool_status
VIS_CFG = fixture("qwen38/config.json")
VIS_TPL = fixture("qwen38/chat_template.jinja")
seed("txt", "org/txt", 8101, config={})
seed("vis", "org/vis", 8102, config=VIS_CFG, template=VIS_TPL,
     user={"type": "vision", "backend": "mlx_vlm"}, machine={"type_evidence": "CLI_EXPLICIT"})
seed("forced", "org/forced", 8103, config=VIS_CFG, user={"modality": "text"})
seed("mtp", "org/mtp", 8104, config=fixture("qwen38-mtp/config.json"))
seed("broken", "org/broken", 8105, config="{not json")
with open(os.path.join(DATA, "models.jsonl"), "a") as fh:
    fh.write(json.dumps({"id": "ghost", "user": {"repo": "org/ghost"},
                         "machine": {"port": 8106}}) + "\\n")
out = {}
ls = model_query.run(ctx, None, Invocation("ls"), {}).payload
out["ls"] = {m["id"]: m["modality_display"] for m in ls["models"]}
out["ls_text"] = ui.human(ls)
info = model_query.run(ctx, None, Invocation("info", "vis"), {}).payload
out["info"] = {k: info.get(k) for k in ("modality", "template", "old_fields", "retired_keys")}
out["info_text"] = ui.human(info)
put_settings({"backend": "mlx_lm", "max_concurrency": 4})
cl = config_cmd.run(ctx, None, Invocation("ls", None), runtime.build_deps(ctx)).payload
out["config_dropped"] = cl.get("dropped")
out["config_text"] = ui.human(cl)
os.makedirs(os.path.join(DATA, "run"), exist_ok=True)
with open(os.path.join(DATA, "run", "vis.json"), "w") as fh:
    fh.write(json.dumps({"pid": 999999, "pgid": 999999, "port": 8102, "started_at": "t",
                         "model_arg": "vis", "type": "model", "schema_ver": 1, "nonce": "n",
                         "backend": "mlx_lm", "backend_auth": False, "max_concurrency": 8,
                         "effort_domain": [], "key_fp": None}))
tool = _tool_status(ctx).payload
out["tool_row"] = [m for m in (tool["models"] or []) if m["id"] == "vis"]
out["tool_text"] = ui.human(tool)
out["up_text"] = ui.human({"kind": "svc", "started": "vis", "pid": 1, "port": 8102,
                           "backend": "mlx_vlm", "max_concurrency": 8})
print(json.dumps(out))
"""


def test_display_surfaces(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60, FIXTURES=FIX, RENDER_PY=sys.executable)

    assert got["ls"] == {"txt": "text", "vis": "vision", "forced": "text",
                         "mtp": "drafter", "broken": "?", "ghost": "—"}, got["ls"]
    assert "模态" in got["ls_text"] and "drafter" in got["ls_text"]

    info = got["info"]
    assert info["modality"]["display"] == "vision"
    assert info["modality"]["configured"] == "auto"
    assert info["modality"]["basis"] == "config.json:vision_config"
    assert info["template"] == {"thinking_default": True,
                                "effort_domain": ["low", "medium", "xhigh"]}
    assert info["old_fields"] == ["user.type", "machine.type_evidence"]
    assert info["retired_keys"] == ["backend"]
    for s in ("思考默认", "effort 值域", "low、medium、xhigh", "mlx model repair"):
        assert s in got["info_text"], f"model info 缺「{s}」：\n{got['info_text']}"

    assert got["config_dropped"] == ["backend"]
    assert "已退役的键：backend" in got["config_text"]

    assert len(got["tool_row"]) == 1, "status 的模型行里没有 vis（注册表读取失败？）"
    row = got["tool_row"][0]
    assert (row["backend"], row["max_concurrency"], row["modality"]) == ("mlx_lm", 8, "auto"), \
        "status 应读运行记录快照（造的是 mlx_lm 记录配 vision 权重），不得现场重判"
    assert "type" not in row, "status 不应再读 1.0.0 的 type 字段"
    assert "后端" in got["tool_text"] and "并发" in got["tool_text"]

    assert "✓ vis 已启动 · mlx_vlm · 并发 8 · port 8102" in got["up_text"], got["up_text"]


def test_model_ls_uses_the_same_override_rule_as_route(payload):
    """spec §8.1：模态覆盖规则只有一个实现 —— `model ls` 调 `route.effective_vision`，不自己算。"""
    import ast
    tree = ast.parse((payload / "lib" / "mlx_server" / "cmd" / "model_query.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "effective_vision"]
    assert calls, "model_query 没调 route.effective_vision —— 覆盖规则出现了第二份实现"
    compares = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Compare)
                and any(isinstance(c, ast.Constant) and c.value in ("text", "vision")
                        for c in n.comparators)]
    assert compares == [], f"model_query 与 text/vision 直接比较（行 {compares}）—— 覆盖规则被复写"
