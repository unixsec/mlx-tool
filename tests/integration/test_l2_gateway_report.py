"""spec §3.4：网关算出的放行网段经门面 `supervisor.merge_record` 并进 api 运行记录；门面只放行这几个键。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + """
import stat
from mlx_server import supervisor
from mlx_server.result import Ok
os.makedirs(os.path.join(DATA, "run"), exist_ok=True)
rec_path = os.path.join(DATA, "run", "api.json")
with open(rec_path, "w") as fh:
    fh.write(json.dumps({"pid": 4242, "pgid": 4242, "port": 8340, "started_at": "t",
                         "model_arg": None, "type": "api", "schema_ver": 1, "nonce": "n"}))
os.chmod(rec_path, 0o644)
fields = {"allowed_nets": [{"net": "127.0.0.0/8"}], "excluded_ifaces": [{"iface": "utun4"}],
          "nets_source": "parsed", "nets_computed_at": 1.0, "nets_error": ""}
r = {"ok": supervisor.merge_record(ctx, "api", fields, pid=4242)}
rec = json.load(open(rec_path))
r["kept"] = [rec["pid"], rec["port"], rec["nets_source"]]
r["mode"] = stat.S_IMODE(os.stat(rec_path).st_mode)
r["pid_mismatch"] = supervisor.merge_record(ctx, "api", dict(fields, nets_source="x"), pid=1)
r["after_mismatch"] = json.load(open(rec_path))["nets_source"]
try:
    supervisor.merge_record(ctx, "api", {"pid": 1})
    r["forbidden"] = "accepted"
except ValueError:
    r["forbidden"] = "ValueError"
r["missing"] = supervisor.merge_record(ctx, "nosuch", fields)
from gateway.app import T1_FACADE, Gateway
four = {k: (lambda *a, **k: Ok(True)) for k in T1_FACADE if k != "supervisor.merge_record"}
try:
    Gateway(None, four)
    r["four_symbols"] = "accepted"
except ValueError:
    r["four_symbols"] = "ValueError"
r["facade"] = list(T1_FACADE)
print(json.dumps(r))
"""


def test_merge_record_is_narrow_and_facade_has_five_symbols(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["ok"] is True and got["kept"] == [4242, 8340, "parsed"]
    assert got["mode"] == 0o600
    assert got["pid_mismatch"] is False and got["after_mismatch"] == "parsed", \
        "记录不属于调用方进程时不得写"
    assert got["forbidden"] == "ValueError", "门面不得允许网关改写 pid 等字段"
    assert got["missing"] is False
    assert got["four_symbols"] == "ValueError"
    assert got["facade"] == ["store.load", "supervisor.running", "supervisor.port_owner",
                             "supervisor.snapshot", "supervisor.merge_record"]
