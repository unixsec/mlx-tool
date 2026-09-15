"""`weights.inspect_config`（spec §2.3）：真实夹具 + 边界样例。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"


def test_inspect_config_cases(payload, sys_py):
    got = run_floor(payload, sys_py, WORLD + """
from mlx_server import weights
from mlx_server.tables import TablesView
attach = [r["model_type"] for r in TablesView(SHARE).drafters()]
def case(name, cfg):
    snap = seed(name, "org/" + name, 8100, config=cfg)
    r = weights.inspect_config(snap, attach)
    return {"unknown": r.kind} if r.is_unknown() else r.value
out = {
  "qwen38": case("qwen38", fixture("qwen38/config.json")),
  "mtp": case("mtp", fixture("qwen38-mtp/config.json")),
  "empty": case("empty", {}),
  "lmo_str": case("lmo", {"vision_config": {"depth": 1}, "language_model_only": "true"}),
  "lmo_true": case("lmot", {"vision_config": {"depth": 1}, "language_model_only": True}),
  "speculators": case("spec", {"speculators_model_type": "eagle3", "architectures": ["X"]}),
  "full_drafter": case("full", {"model_type": "qwen3", "architectures": ["Qwen3ForCausalLM"]}),
  "no_arch": case("noarch", {"model_type": "qwen3"}),
  "not_object": case("list", "[]"),
}
snap = seed("missing", "org/missing", 8101)
r = weights.inspect_config(snap, attach)
out["missing"] = {"unknown": r.kind}
print(json.dumps(out))
""", FIXTURES=FIX, RENDER_PY="")
    q = got["qwen38"]
    assert (q["vision"], q["standalone"], q["attachment"]) == (True, True, False), q
    assert q["vision_src"] == "config.json:vision_config"
    m = got["mtp"]
    assert (m["vision"], m["standalone"], m["attachment"]) == (False, False, True), m
    assert m["model_type"] == "qwen3_5_mtp"
    assert got["empty"]["vision"] is False
    assert got["lmo_str"]["vision"] is None, "language_model_only 非布尔必须判不出"
    assert got["lmo_true"]["vision"] is False
    assert got["speculators"]["standalone"] is False
    assert got["full_drafter"]["attachment"] is False
    assert got["no_arch"]["attachment"] is True, "缺 architectures 的 drafter 不能当完整模型"
    assert got["not_object"] == {"unknown": "PARSE_FAILED"}
    assert got["missing"] == {"unknown": "PARSE_FAILED"}, "缺 config.json 不得猜 text"
