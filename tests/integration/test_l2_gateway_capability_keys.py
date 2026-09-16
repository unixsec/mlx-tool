"""spec §6.1 媒体上限键、§3.4 运行记录能力字段、§4.8 进程环境。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

ROWS = {"api.media_max_mb": ("int", "1-2048", "128"),
        "api.media_max_parts": ("int", "1-256", "32"),
        "api.inflight_max_mb": ("int", "64-65536", "2048")}


def test_keys_declared(payload):
    rows = {ln.split("\t")[0]: ln.split("\t") for ln in
            (payload / "share" / "schema.tsv").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
    for key, (typ, dom, default) in ROWS.items():
        f = rows[key]
        assert (f[1], f[2], f[3], f[4], f[5], f[6]) == (typ, dom, default, "user", "global", "gateway")
        assert f[10].strip()


PROG = WORLD + """
import io, sys
from mlx_server import backendarg, route, schema, supervisor
from mlx_server.cmd.svc import Invocation, run
from mlx_server.tables import TablesView
put_settings({"api.token": "T" * 43})
deps = {"backendarg_spec": backendarg.spec}
a = run(ctx, None, Invocation("up", "api", dry_run=True), deps).payload["env"]
put_settings({"api.token": "T" * 43, "api.media_max_mb": 8, "api.media_max_parts": 2,
              "api.inflight_max_mb": 64})
b = run(ctx, None, Invocation("up", "api", dry_run=True), deps).payload["env"]
tv = TablesView(SHARE)
B = {r["backend"]: r for r in tv.backends()}
v = schema.defaults_from(tv); v.update({"port": 8123})
cfg = schema.ServiceConfig("m", v, {}, "/py")
rec = {}
for name in ("mlx_lm", "mlx_vlm"):
    supervisor.write_record(ctx, backendarg.spec("m", cfg, route.Route(name, B[name], "R?", False, [], "", 1.0, 8, None)),
                            1, 1, 8123, "t", "n")
    with open(os.path.join(DATA, "run", "m.json")) as fh:
        r = json.load(fh)
    rec[name] = [r["reads_completion_tokens"], r["native_template_kwargs"]]
from gateway.app import LIMIT_DEFAULTS, Gateway
from mlx_server.result import Ok
facade = {k: (lambda *x, **y: Ok(True)) for k in ("store.load", "supervisor.running",
          "supervisor.port_owner", "supervisor.snapshot", "supervisor.merge_record")}
limits = Gateway(None, facade, token="t" * 43, limits={"media_max_mb": 1}).limits
import gateway.__main__ as gm
err, real = io.StringIO(), sys.stderr
sys.stderr = err
os.environ["MLX_GATEWAY_MEDIA_MAX_MB"] = "abc"
bad = gm._int_env("MLX_GATEWAY_MEDIA_MAX_MB", 128)
os.environ["MLX_GATEWAY_MEDIA_MAX_MB"] = "7"
good = gm._int_env("MLX_GATEWAY_MEDIA_MAX_MB", 128)
missing = gm._int_env("MLX_GATEWAY_NOPE", 32)
sys.stderr = real
keys = ("MLX_GATEWAY_MEDIA_MAX_MB", "MLX_GATEWAY_MEDIA_MAX_PARTS", "MLX_GATEWAY_INFLIGHT_MAX_MB")
print(json.dumps({"a": [a[k] for k in keys], "b": [b[k] for k in keys], "rec": rec,
                  "defaults": LIMIT_DEFAULTS, "limits": limits,
                  "env": [bad, good, missing, "bad_env" in err.getvalue()]}))
"""


def test_env_record_and_limits(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["a"] == ["128", "32", "2048"] and got["b"] == ["8", "2", "64"]
    assert got["rec"] == {"mlx_lm": [True, True], "mlx_vlm": [False, False]}
    assert got["defaults"] == {"media_max_mb": 128, "media_max_parts": 32, "inflight_max_mb": 2048}
    assert got["limits"] == {"media_max_mb": 1, "media_max_parts": 32, "inflight_max_mb": 2048}
    assert got["env"] == [128, 7, 32, True]
