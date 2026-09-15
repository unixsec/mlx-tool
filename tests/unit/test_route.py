"""`route.decide`（spec §2.4）。规则表在测试内**冻结为字面量**，不从 route.py 导入 ——
表与实现同步写错时，从实现导入的表会跟着错。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402
from floor import run_floor  # noqa: E402

FROZEN_RULES = {"S1", "U1", "U2", "R0", "R1", "R2", "R3", "R4", "P1", "P2", "P3"}
KEY = "K" * 43

PROG = """
import json
from mlx_server import route
from mlx_server.result import Ok, Unknown
from mlx_server.tables import TablesView
B = {r["backend"]: r for r in TablesView("@PAYLOAD@/share").backends()}
TXT = {"vision": False, "vision_src": "t", "standalone": True, "attachment": False, "model_type": "qwen3"}
VIS = dict(TXT, vision=True, vision_src="config.json:vision_config", model_type="qwen3_5")
MTP = dict(TXT, standalone=False, attachment=True, model_type="qwen3_5_mtp")
FULL = dict(TXT, attachment=False)
TPL = Ok({"thinking_default": True, "effort_domain": ["low", "medium", "xhigh"]})
def cfg(**kw):
    base = {"modality": "auto", "max_concurrency": 8, "backend.api_key": "@KEY@"}
    base.update(kw)
    return base
ALL = ["mlx", "mlx_lm", "mlx_vlm"]
def go(facts, draft=None, c=None, pk=ALL, size=1.0, tpl=TPL):
    r = route.decide("m", facts, tpl, draft, c or cfg(), pk, B, size)
    if isinstance(r, route.Route):
        return {"rule": r.rule, "backend": r.backend, "thinking": r.thinking,
                "effort": r.effort_domain, "fp": r.key_fp, "conc": r.max_concurrency,
                "note": r.template_note}
    return {"key": r.msg_key, "rc": r.rc}
cases = {
  "S1": go(MTP),
  "U1": go(dict(TXT, vision=None)),
  "U1_overridden": go(dict(TXT, vision=None), c=cfg(modality="text")),
  "U2": go(TXT, draft=("d", None)),
  "R0": go(TXT, draft=("d", MTP)),
  "R1": go(VIS, draft=("d", FULL)),
  "R2": go(TXT, draft=("d", FULL)),
  "R3": go(VIS),
  "R4": go(TXT),
  "R4_forced": go(VIS, c=cfg(modality="text")),
  "R3_forced": go(TXT, c=cfg(modality="vision")),
  "P1": go(VIS, pk=["mlx", "mlx_lm"]),
  "P2": go(VIS, c=cfg(**{"backend.api_key": ""})),
  "P3": go(VIS, size=None),
  "order_S1_U1": go(dict(MTP, vision=None)),
  "order_R0_R1": go(VIS, draft=("d", MTP)),
  "order_P1_P2": go(VIS, pk=["mlx_lm"], c=cfg(**{"backend.api_key": ""})),
  "order_P2_P3": go(VIS, c=cfg(**{"backend.api_key": ""}), size=None),
  "tpl_unknown": go(VIS, tpl=Unknown("TIMEOUT", "x")),
  "vis_text_model_no_key_ok": go(TXT, c=cfg(**{"backend.api_key": ""})),
}
cases["ev"] = [route.effective_vision(v, m) for v, m in
               [(True, "text"), (False, "vision"), (None, "auto"), (True, "auto"), (False, None)]]
print(json.dumps(cases))
"""


def test_route_rules_frozen(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, KEY=KEY)
    assert got["S1"] == {"key": "svc.not_standalone_model", "rc": 6}
    assert got["U1"] == {"key": "svc.inspect_undeterminable", "rc": 9}
    assert got["U1_overridden"]["rule"] == "R4", "显式 modality 应越过判不出"
    assert got["U2"] == {"key": "svc.draft_inspect_undeterminable", "rc": 9}
    assert got["R0"] == {"key": "svc.draft_unsupported", "rc": 6}
    assert got["R1"] == {"key": "svc.draft_backend_conflict", "rc": 6}
    assert (got["R2"]["rule"], got["R2"]["backend"]) == ("R2", "mlx_lm")
    assert (got["R3"]["rule"], got["R3"]["backend"]) == ("R3", "mlx_vlm")
    assert got["R3"]["thinking"] is True and got["R3"]["effort"] == ["low", "medium", "xhigh"]
    assert got["R3"]["fp"] and len(got["R3"]["fp"]) == 12 and got["R3"]["conc"] == 8
    assert (got["R4"]["rule"], got["R4"]["backend"]) == ("R4", "mlx_lm")
    assert got["R4"]["thinking"] is False and got["R4"]["fp"] is None
    assert got["R4_forced"]["rule"] == "R4" and got["R3_forced"]["rule"] == "R3"
    assert got["P1"] == {"key": "svc.backend_missing", "rc": 6}
    assert got["P2"] == {"key": "svc.backend_key_missing", "rc": 6}
    assert got["P3"] == {"key": "svc.size_undeterminable", "rc": 9}
    assert got["order_S1_U1"]["key"] == "svc.not_standalone_model"
    assert got["order_R0_R1"]["key"] == "svc.draft_unsupported"
    assert got["order_P1_P2"]["key"] == "svc.backend_missing"
    assert got["order_P2_P3"]["key"] == "svc.backend_key_missing"
    assert got["tpl_unknown"]["thinking"] is False and got["tpl_unknown"]["effort"] == []
    assert got["tpl_unknown"]["note"] == "x" and got["R3"]["note"] is None, \
        "模板判不出要带原因，判得出不带"
    assert got["vis_text_model_no_key_ok"]["rule"] == "R4", "mlx_lm 不需要后端密钥"
    assert got["ev"] == [False, True, None, True, False]

    hit = {k.split("_")[0] for k in got if k[:2] in {"S1", "U1", "U2", "R0", "R1", "R2",
                                                       "R3", "R4", "P1", "P2", "P3"}}
    Cardinality.check("命中的规则 ↔ 冻结规则表", sorted(hit), sorted(FROZEN_RULES), rel="equal")
