"""`config validate` 与 `up` 同一个值域判定；`config export --json` 的产物能被 `config import` 直接读回。

坏样本（终审 C I2）：`settings.json` 里 `max_tokens: 0` 时 `config validate` 报 ✓ ok、rc 0，
`up` 报「配置项 max_tokens 非法」—— validate 只在合成抛错时才标 failed，而那条分支
对表内键不可达。判据按**类**：每个 int 键各造一个越下界的值，validate 必须把它标 failed
（rc 65），且与 `up` 的求值点（`svc.VALIDATORS["生效配置"]`）对同一个键给出同一个结论。

坏样本（终审 C I3）：手册的换机流程 `config export --json > f` → `config import --from=f`
导不进任何东西且 rc 0 —— import 把信封的 kind/skipped/target/values 当成配置键拒掉。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
from mlx_server import schema, store
from mlx_server.cmd import config, svc
from mlx_server.cmd.svc import Invocation
from mlx_server.tables import open_tables

defs = schema.keydefs(open_tables(ctx))
seed("m1", "org/m1", 8151, config="{}")
out = {"validate": {}, "roundtrip": {}}
int_global = sorted(k for k, d in defs.items() if d["type"] == "int" and d["scope"] == "user"
                    and d["level"] in ("global", "both"))
out["keys"] = int_global
for k in int_global:
    lo = int(defs[k]["domain"].partition("-")[0])
    put_settings({k: lo - 1})
    v = config.run(ctx, None, Invocation("validate", None), {})
    row = [r for r in v.payload["rows"] if r[0] == k][0]
    # `up` 的求值点：模型目标与网关目标都试（键可能只对其中之一生效）
    up_bad = []
    for tgt in ("m1", "api"):
        rec = None if tgt == "api" else store.load(ctx, store.MODELS).by_id(tgt)
        st = {"ctx": ctx, "record": rec}
        r = svc.VALIDATORS["生效配置"](st, Invocation("up", tgt), {})
        if r is not None and getattr(r, "msg_key", None) == "config.invalid":
            up_bad.append(tgt)
    out["validate"][k] = {"rc": v.rc, "verdict": row[1], "failed": v.payload["failed"],
                          "up_refuses": bool(up_bad)}
put_settings({})
v = config.run(ctx, None, Invocation("validate", None), {})
out["clean"] = [v.rc, v.payload["failed"]]

# export → import 往返：全局改过的键原样回到一个空的 settings
put_settings({"max_tokens": 4096, "api.media_max_parts": 7})
exp = config.run(ctx, None, Invocation("export", None), {})
src = os.path.join(_ROOT, "exp.json")
json.dump(exp.payload, open(src, "w"))
put_settings({})
imp = config.run(ctx, None, Invocation("import", None, args={"from": src}), {})
out["roundtrip"] = {"rc": imp.rc, "applied": imp.payload.get("applied"),
                    "rejected": imp.payload.get("rejected"), "after": store.load_settings(ctx)}
json.dump({"kind": "config_export", "values": 5}, open(src, "w"))
bad = config.run(ctx, None, Invocation("import", None, args={"from": src}), {})
out["bad_envelope"] = [bad.rc, getattr(bad, "msg_key", None)]
print(json.dumps(out, ensure_ascii=False))
"""


def test_validate_flags_out_of_domain_values_like_up(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=120)
    assert "max_tokens" in got["keys"] and len(got["keys"]) >= 5, got["keys"]
    for k, r in got["validate"].items():
        assert r["up_refuses"], f"{k}：夹具没让 up 拒绝（越下界值应非法）：{r}"
        assert r["rc"] == 65 and r["verdict"] == "failed" and r["failed"] == [k], \
            f"{k}：up 判非法，validate 也必须标 failed（rc 65，只标这一个键）：{r}"
    assert got["clean"] == [0, []], f"全部出厂值时 validate 应全绿：{got['clean']}"


def test_export_json_imports_back(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=120)
    rt = got["roundtrip"]
    assert rt["rc"] == 0 and rt["rejected"] == [], f"信封不应被当成配置键拒掉：{rt}"
    assert rt["after"] == {"max_tokens": 4096, "api.media_max_parts": 7}, f"往返后值不一致：{rt}"
    assert got["bad_envelope"] == [6, "config.import_bad_json"], got["bad_envelope"]


PORTS = WORLD + r"""
from mlx_server import ports, store
from mlx_server.cmd import config, model_query
from mlx_server.cmd.svc import Invocation
seed("qa", "org/qa", 8150, config="{}")
seed("qb", "org/qb", 8160, config="{}")
out = {}
def setp(mid, v):
    r = config.run(ctx, None, Invocation("set", mid, args={"key": "port", "value": str(v)}), {})
    return [r.rc, getattr(r, "msg_key", None)]
out["dup"] = setp("qb", 8150)
out["gateway"] = setp("qb", ctx.gateway_port)
out["free"] = setp("qb", 8170)
out["own_registered"] = setp("qa", 8150)
# 旧版本留下的重复自设端口（绕过了 set 时检查）：up 的登记面互斥要看见它
store.commit(ctx, store.MODELS, store.ModelsTx("qb", {"port": 8150}, user_keys=("port",)))
recs = store.load(ctx, store.MODELS).records
c = ports.conflict_check(ctx, recs)
out["conflict"] = [getattr(c, "rc", None), getattr(c, "msg_key", None)]
a = ports.alloc(ctx, recs)
out["alloc"] = a.value
ls = model_query.run(ctx, None, Invocation("ls", None), {})
out["ls_ports"] = {m["id"]: m["port"] for m in ls.payload["models"]}
print(json.dumps(out))
"""


def test_model_set_port_joins_the_port_exclusion(payload, sys_py):
    """终审 C I4：`config set <id> --key=port` 写进 user 段，而互斥检查与分配只看登记的
    machine 段 —— 两个模型可以同端口、甚至占网关端口，`up` 照样放行。"""
    got = run_floor(payload, sys_py, PORTS, timeout=60)
    assert got["dup"] == [7, "model.uniqueness"], got
    assert got["gateway"] == [7, "ports.gateway_conflict"], got
    assert got["free"][0] == 0 and got["own_registered"][0] == 0, got
    assert got["conflict"] == [7, "ports.conflict"], f"生效端口重复时登记面互斥必须报冲突：{got}"
    assert got["alloc"] not in (8150, 8160, 8170), f"分配不得给出已被自设占用的端口：{got}"
    assert got["ls_ports"] == {"qa": 8150, "qb": 8150}, f"model ls 端口列应显示生效端口：{got}"


MINORS = WORLD + r"""
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
seed("m1", "org/m1", 8151, config="{}")
msgs = {}
for ln in open(os.path.join(SHARE, "messages.tsv"), encoding="utf-8"):
    if ln.startswith("config.level_mismatch\t"):
        tpl = ln.rstrip("\n").split("\t", 1)[1]
out = {}
for name, tgt, key, val in (("global_port", "port", "port", "9000"),
                            ("model_bind", "m1", "api.bind", "10.1.1.1")):
    r = config.run(ctx, None, Invocation("set", tgt, args={"key": key, "value": val}), {})
    out[name] = [r.rc, r.msg_key, tpl.format(**r.args)]
put_settings({"max_tokens": 4096})
d = config.run(ctx, None, Invocation("diff", None), {})
out["diff_keys"] = [row["key"] for row in d.payload["rows"]]
print(json.dumps(out, ensure_ascii=False))
"""


def test_level_mismatch_hint_and_diff_rows(payload, sys_py):
    """终审 C M1：level_mismatch 只给正确的那一种写法；C M4：全局 diff 不列 cli 层（网关端口）。"""
    got = run_floor(payload, sys_py, MINORS, timeout=60)
    rc, key, text = got["global_port"]
    assert (rc, key) == (6, "config.level_mismatch"), got
    assert "<模型id> --key=port" in text and "config set port --value" not in text, text
    rc, key, text = got["model_bind"]
    assert (rc, key) == (6, "config.level_mismatch"), got
    assert "config set api.bind --value" in text and "<模型id>" not in text, text
    assert got["diff_keys"] == ["max_tokens"], f"diff 只列改过的键：{got['diff_keys']}"
