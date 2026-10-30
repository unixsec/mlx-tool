"""`config get/ls` 对每个键报的生效值与来源，必须等于 `up` 用的那个。

类，不是实例：此前只钉了 `port`（登记端口），而同一份漂移还有三处 ——
api 目标的端口（`up api` 用 `ctx.gateway_port`，cli 层）、环境里的网关令牌、
`level` 列（`modality` 是模型级、`api.token` 是全局级，`up` 按它过滤两层）。
根因是 `config._layers` 与 `svc._v_config` 各有一份层构造；现在共用
`schema.build_layers`，本守卫从两头夹它：

① 对账：config get/ls 与 `up` 的求值点（`svc.VALIDATORS["生效配置"]`，`up`/`restart`
   实际 dispatch 的那一步）逐键、逐目标（无覆盖的模型 / 全键覆盖的模型 / api）、
   逐场景（有无环境令牌）比较值与来源 —— 密钥键比掩码后的值与来源，不比原值；
② 判据：期望来源只从 `schema.tsv` 的 scope/level 列推出，不看任何实现 ——
   否则「两边一起错」（比如共享层不看 level）也会绿。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

TOKEN_ENV = "E" * 40

PROG = WORLD + r"""
from mlx_server import schema
from mlx_server.cmd import config, svc
from mlx_server.cmd.svc import Invocation
from mlx_server.ctx import Ctx
from mlx_server.tables import open_tables
from mlx_server import store

defs = schema.keydefs(open_tables(ctx))
user_keys = sorted(k for k, d in defs.items() if d["scope"] == "user")

def sample(k, tag):
    # 每个键造一个**合法且不同于出厂值**的值；tag 区分 settings(0) / 模型(1)
    d = defs[k]; dom = d["domain"].strip(); dflt = schema.defaults_from(open_tables(ctx)).get(k)
    if d["type"] == "int":
        lo = int(dom.partition("-")[0] or 0)
        v = lo + 1 + tag
        if v == dflt:
            v += 2
        return v
    if dom == "#ip":
        return ["10.1.1.1", "10.2.2.2"][tag]
    if dom == "#cidrs":
        return ["10.0.0.0/8", "172.16.0.0/12"][tag]
    if k == "api.allow_ifaces":             # 正则域按键给样本：接口名不是令牌的形状
        return ["en7", "en8"][tag]
    if dom.startswith("^"):
        return ("S", "U")[tag] * 40
    if "|" in dom:
        opts = [o for o in dom.split("|") if o != dflt]
        return opts[tag % len(opts)]
    return ("s-", "u-")[tag] + k
settings = {k: sample(k, 0) for k in user_keys}
model_user = {k: sample(k, 1) for k in user_keys}
for k in user_keys:
    assert schema.check_domain(k, settings[k], defs) is None, (k, settings[k])
    assert schema.check_domain(k, model_user[k], defs) is None, (k, model_user[k])
put_settings(settings)
seed("plain", "org/plain", 8151, config="{}")
seed("full", "org/full", 8152, config="{}", user=model_user)

def table(cfg_values, cfg_sources):
    m = schema.masked(dict(cfg_values), defs)
    return {k: [m[k], cfg_sources[k]] for k in user_keys}

out = {"settings": settings, "model_user": model_user, "scenarios": {}}
for scen, tok in (("no_env_token", ""), ("env_token", "@TOKEN@")):
    c = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None, share_root=SHARE,
            gateway_token_env=tok)
    res = {}
    for tgt in ("plain", "full", "api"):
        rec = None if tgt == "api" else store.load(c, store.MODELS).by_id(tgt)
        st = {"ctx": c, "record": rec}
        refused = svc.VALIDATORS["生效配置"](st, Invocation("up", tgt), {})
        assert refused is None, (scen, tgt, getattr(refused, "msg_key", refused))
        oracle = table(st["cfg"].values, st["cfg"].sources)
        get, ls = {}, None
        for k in user_keys:
            g = config.run(c, None, Invocation("get", None if tgt == "api" else tgt,
                                               args={"key": k}), {})
            assert g.rc == 0, (scen, tgt, k, g)
            get[k] = [g.payload["value"], g.payload["source"]]
        l = config.run(c, None, Invocation("ls", None if tgt == "api" else tgt), {})
        ls = {k: [l.payload["values"][k], l.payload["sources"][k]] for k in user_keys}
        res[tgt] = {"oracle": oracle, "get": get, "ls": ls}
    out["scenarios"][scen] = res
print(json.dumps(out))
""".replace("@TOKEN@", TOKEN_ENV)


def _schema_rows(payload):
    rows = {}
    for ln in (payload / "share" / "schema.tsv").read_text().splitlines():
        if ln and not ln.startswith("#"):
            c = ln.split("\t")
            rows[c[0]] = {"scope": c[4], "level": c[5], "secret": c[8]}
    return rows


def _expected_source(key, level, target, env_token):
    """只从 scope/level 推出的期望来源（不看实现）。"""
    if target == "api":
        if key == "port":
            return "install"          # 安装时写进 constants.env 的网关端口
        if key == "api.token" and env_token:
            return "env"              # shell 里导出的 MLX_GATEWAY_TOKEN
        return "machine" if level in ("global", "both") else "default"
    if target == "full":
        return "user" if level in ("model", "both") else "machine"
    # plain：没有 user 段；登记端口落 machine，全局端口设置不吃
    if key == "port":
        return "machine"
    return "machine" if level in ("global", "both") else "default"


def test_config_reads_equal_what_up_uses_for_every_key(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=120)
    rows = {k: v for k, v in _schema_rows(payload).items() if v["scope"] == "user"}
    assert len(rows) >= 15, "schema 没读对"
    assert set(got["scenarios"]) == {"no_env_token", "env_token"}
    bad = []
    for scen, per_target in got["scenarios"].items():
        assert set(per_target) == {"plain", "full", "api"}
        for tgt, r in per_target.items():
            assert set(r["oracle"]) == set(rows), "对账面没覆盖全部可读键"
            for k, info in rows.items():
                oracle = r["oracle"][k]
                if r["get"][k] != oracle:
                    bad.append(f"{scen}/{tgt}/{k}: get={r['get'][k]} up={oracle}")
                if r["ls"][k] != oracle:
                    bad.append(f"{scen}/{tgt}/{k}: ls={r['ls'][k]} up={oracle}")
                want = _expected_source(k, info["level"], tgt, scen == "env_token")
                if oracle[1] != want:
                    bad.append(f"{scen}/{tgt}/{k}: 来源 {oracle[1]}，按 scope/level 应为 {want}")
    assert not bad, "config 读侧与 up 的生效配置不一致：\n  " + "\n  ".join(bad[:30])
    # 阳性：登记端口 / 网关端口 / 环境令牌 / 模型自设值都真的出现过
    s = got["scenarios"]
    assert s["no_env_token"]["plain"]["get"]["port"] == [8151, "machine"]
    assert s["no_env_token"]["api"]["get"]["port"] == [8340, "install"]
    assert s["env_token"]["api"]["get"]["api.token"][1] == "env"
    assert s["no_env_token"]["api"]["get"]["api.token"][1] == "machine"
    assert s["no_env_token"]["full"]["get"]["modality"] == [got["model_user"]["modality"], "user"]
    # 密钥永不以原值出现在读侧
    for scen in s.values():
        for r in scen.values():
            for k, i in rows.items():
                if i["secret"] == "yes":
                    assert json.dumps(r["get"][k]).count("U" * 40) == 0
                    assert json.dumps(r["get"][k]).count("S" * 40) == 0
                    assert json.dumps(r["get"][k]).count(TOKEN_ENV) == 0
