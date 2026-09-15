"""spec §6.1 网关暴露面键与 §4.8 进程环境。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

SPEC_ROWS = {
    "api.bind": ("str", "#ip", "0.0.0.0", "user", "global", "gateway", "yes", "no"),
    "api.allow_cidrs": ("str", "#cidrs", "", "user", "global", "gateway", "yes", "no"),
    "api.tls_cert": ("str", "<证书路径>", "", "user", "global", "gateway", "no", "no"),
    "api.tls_key": ("str", "<私钥路径>", "", "user", "global", "gateway", "no", "no"),
    "api.max_body_mb": ("int", "1-2048", "256", "user", "global", "gateway", "yes", "no"),
}


def test_keys_declared_with_spec_values(payload):
    rows = {}
    for ln in (payload / "share" / "schema.tsv").read_text().splitlines():
        if ln.strip() and not ln.startswith("#"):
            f = ln.split("\t")
            rows[f[0]] = f
    for key, want in SPEC_ROWS.items():
        assert key in rows, f"schema.tsv 缺 {key}"
        assert tuple(rows[key][1:9]) == want, f"{key}：{rows[key][1:9]} ≠ {want}"
        assert rows[key][10].strip(), f"{key} 的 readers 为空"


def test_domain_matrix(payload, sys_py):
    got = run_floor(payload, sys_py, """
        import json
        from mlx_server import schema
        from mlx_server.tables import TablesView
        defs = schema.keydefs(TablesView("@PAYLOAD@/share"))
        cases = [("api.bind", "0.0.0.0"), ("api.bind", "::"), ("api.bind", "127.0.0.1"),
                 ("api.bind", "localhost"), ("api.bind", "192.168.2.300"), ("api.bind", ""),
                 ("api.allow_cidrs", "10.0.0.0/8, fd00::/8"), ("api.allow_cidrs", ""),
                 ("api.allow_cidrs", "10.0.0.0/33"), ("api.allow_cidrs", "10.0.0.0/8,nope"),
                 ("api.max_body_mb", 0), ("api.max_body_mb", 2049), ("api.max_body_mb", 256),
                 ("api.tls_cert", "/any/path.pem")]
        print(json.dumps([[k, v, schema.check_domain(k, v, defs)] for k, v in cases]))
    """)
    verdict = {(k, str(v)): bad is None for k, v, bad in got}
    assert verdict == {
        ("api.bind", "0.0.0.0"): True, ("api.bind", "::"): True, ("api.bind", "127.0.0.1"): True,
        ("api.bind", "localhost"): False, ("api.bind", "192.168.2.300"): False,
        ("api.bind", ""): True,
        ("api.allow_cidrs", "10.0.0.0/8, fd00::/8"): True, ("api.allow_cidrs", ""): True,
        ("api.allow_cidrs", "10.0.0.0/33"): False, ("api.allow_cidrs", "10.0.0.0/8,nope"): False,
        ("api.max_body_mb", "0"): False, ("api.max_body_mb", "2049"): False,
        ("api.max_body_mb", "256"): True, ("api.tls_cert", "/any/path.pem"): True,
    }


def test_api_process_env_carries_exposure_settings(payload, sys_py):
    got = run_floor(payload, sys_py, WORLD + """
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
deps = {"backendarg_spec": backendarg.spec}
put_settings({"api.token": "T" * 43})
a = run(ctx, None, Invocation("up", "api", dry_run=True), deps).payload["env"]
put_settings({"api.token": "T" * 43, "api.bind": "::", "api.allow_cidrs": "192.168.2.0/24",
              "api.tls_cert": "/c.pem", "api.tls_key": "/k.pem", "api.max_body_mb": 16})
b = run(ctx, None, Invocation("up", "api", dry_run=True), deps).payload["env"]
put_settings({"api.token": "T" * 43, "api.bind": "localhost"})
c = run(ctx, None, Invocation("up", "api", dry_run=True), deps)
print(json.dumps({"a": a, "b": b, "c": [c.rc, c.msg_key]}))
""")
    keys = ("MLX_GATEWAY_BIND", "MLX_GATEWAY_ALLOW_CIDRS", "MLX_GATEWAY_TLS_CERT",
            "MLX_GATEWAY_TLS_KEY", "MLX_GATEWAY_MAX_BODY_MB")
    assert tuple(got["a"][k] for k in keys) == ("0.0.0.0", "", "", "", "256")
    assert tuple(got["b"][k] for k in keys) == ("::", "192.168.2.0/24", "/c.pem", "/k.pem", "16")
    assert got["c"] == [6, "config.invalid"], "api.bind 值域非法应在校验段拒绝"
