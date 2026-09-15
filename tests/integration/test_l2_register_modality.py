"""登记路径不再判定类型（spec §6.2）：`--type` 可选，写 per-model `modality`。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + """
from mlx_server import store, tables
from mlx_server.cmd.model_acquire import run
from mlx_server.cmd.svc import Invocation
def reg(mid, dry=False, **kw):
    inv = Invocation("register", mid, args=dict({"repo": "org/" + mid}, **kw), dry_run=dry)
    out = run(ctx, None, inv, {})
    return {"rc": out.rc, "key": getattr(out, "msg_key", None),
            "payload": getattr(out, "payload", None)}
r = {"plain": reg("plain"), "vis": reg("vis", type="vision"),
     "bad": reg("bad", type="banana"), "dry": reg("dry", dry=True, type="text")}
r["recs"] = {x.id: {"user": dict(x.user), "machine": dict(x.machine)}
             for x in store.load(ctx, store.MODELS).records}
r["has_model_types"] = hasattr(tables.TablesView, "model_types")
print(json.dumps(r))
"""


def test_register_without_type_succeeds_and_type_writes_modality(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["plain"]["rc"] == 0, got["plain"]
    assert got["plain"]["payload"]["modality"] == "auto"
    assert got["recs"]["plain"]["user"] == {"repo": "org/plain"}, "不给 --type 不应写任何类型字段"
    assert "type_evidence" not in got["recs"]["plain"]["machine"]
    assert got["recs"]["vis"]["user"]["modality"] == "vision"
    assert (got["bad"]["rc"], got["bad"]["key"]) == (6, "config.invalid")
    assert "bad" not in got["recs"], "值域非法却落盘了"
    assert got["dry"]["payload"]["modality"] == "text" and "dry" not in got["recs"]
    assert got["has_model_types"] is False


def test_types_table_retired_from_payload(payload):
    assert not (payload / "share" / "types.tsv").exists()
    assert "types.tsv" not in (payload / "share" / "manifest.tsv").read_text()
    schema_keys = [ln.split("\t")[0] for ln in
                   (payload / "share" / "schema.tsv").read_text().splitlines()
                   if ln.strip() and not ln.startswith("#")]
    assert "backend" not in schema_keys, "backend 由判定产出，不再是配置键"
