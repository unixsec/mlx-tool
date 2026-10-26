"""`config set` / `config import` 的 level 闸门：写得进去 ⇔ 读侧会纳入。

坏样本：`config set <模型> api.bind` 写进模型 user 段而 `up` 忽略它；
`config set modality`（模型级）写进全局 settings 而 machine 层按 level 过滤掉 ——
两个方向都是「设了什么都不会发生」的死旋钮。

判据取 `schema.build_layers`（`up` 与 `config get` 共用的层构造）：对每个可写键 ×
{全局, 模型} 两种目标，用同一个 level 语义**独立**推出「这一层会不会纳入该键」，
断言 `set` 成功 ⇔ 纳入；`import`（只写全局层）同理；被拒时盘上零变化。
另：读侧对未登记 / 拼错的模型 id 报 model.not_found，不按网关构造层。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
from mlx_server import schema, store
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
from mlx_server.tables import open_tables

tv = open_tables(ctx)
defs = schema.keydefs(tv)
dflt = schema.defaults_from(tv)
writable = sorted(k for k in defs if schema.writable(k, defs))

def sample(k):
    d = defs[k]; dom = d["domain"].strip()
    if d["type"] == "int":
        v = int(dom.partition("-")[0] or 0) + 1
        return v + 2 if v == dflt.get(k) else v
    if dom == "#ip": return "10.1.1.1"
    if dom == "#cidrs": return "10.0.0.0/8"
    if dom.startswith("^"): return "S" * 40
    if "|" in dom: return [o for o in dom.split("|") if o != dflt.get(k)][0]
    return "v-" + k

def disk():
    out = {}
    for n in ("settings.json", "models.jsonl"):
        p = os.path.join(DATA, n)
        out[n] = open(p).read() if os.path.exists(p) else None
    return out

seed("m1", "org/m1", 8151, config="{}")
res = {"keys": writable, "set": {}, "import": {}, "reads": {}}
for k in writable:
    v = sample(k)
    assert schema.check_domain(k, v, defs) is None, (k, v)
    for form in ("global", "model"):
        before = disk()
        inv = Invocation("set", k if form == "global" else "m1",
                         args={"key": k, "value": str(v)})
        out = config.run(ctx, None, inv, {})
        # 判据：同一个 level 语义，经 build_layers 独立推出
        if form == "global":
            layers, _ = schema.build_layers(defs, {k: v}, False, runtime=False)
            included = k in (layers.get("machine") or {})
        else:
            layers, _ = schema.build_layers(defs, {}, True, rec_user={k: v})
            included = k in (layers.get("user") or {})
        res["set"][k + "/" + form] = {
            "rc": out.rc, "key": getattr(out, "msg_key", None), "included": included,
            "changed": disk() != before}
        if out.rc == 0:                                   # 复位，互不干扰
            config.run(ctx, None, Invocation("unset", k if form == "global" else "m1",
                                             args={"key": k}), {})
    # import 只写全局层
    src = os.path.join(_ROOT, "imp.json")
    json.dump({k: v}, open(src, "w"))
    before = disk()
    out = config.run(ctx, None, Invocation("import", None, args={"from": src}), {})
    layers, _ = schema.build_layers(defs, {k: v}, False, runtime=False)
    res["import"][k] = {"planned": k in out.payload["planned"],
                        "included": k in (layers.get("machine") or {}),
                        "changed_when_rejected": (k not in out.payload["planned"]
                                                  and disk() != before)}
    config.run(ctx, None, Invocation("unset", k, args={"key": k}), {})
for verb in ("ls", "get", "validate", "diff", "export"):
    out = config.run(ctx, None, Invocation(verb, "nosuchmodel", args={"key": "port"}), {})
    res["reads"][verb] = [out.rc, getattr(out, "msg_key", None)]
print(json.dumps(res))
"""


def test_set_and_import_succeed_iff_the_layer_reads_the_key(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=120)
    keys = got["keys"]
    assert len(keys) >= 15
    bad = []
    seen_ok = {"global": 0, "model": 0}
    seen_refused = {"global": 0, "model": 0}
    for ident, r in got["set"].items():
        k, form = ident.rsplit("/", 1)
        if (r["rc"] == 0) != r["included"]:
            bad.append(f"set {ident}: rc={r['rc']} {r['key']}，但层{'会' if r['included'] else '不会'}纳入该键")
        if r["rc"] != 0:
            seen_refused[form] += 1
            if r["key"] != "config.level_mismatch":
                bad.append(f"set {ident}: 拒绝原因应是 config.level_mismatch，实得 {r['key']}")
            if r["changed"]:
                bad.append(f"set {ident}: 被拒却改了盘")
        else:
            seen_ok[form] += 1
    for k, r in got["import"].items():
        if r["planned"] != r["included"]:
            bad.append(f"import {k}: planned={r['planned']} 但全局层{'会' if r['included'] else '不会'}纳入")
        if r["changed_when_rejected"]:
            bad.append(f"import {k}: 被拒却改了盘")
    assert not bad, "\n".join(bad)
    # 两个方向都真被拒过、也真成功过（否则「⇔」可能由单边空真满足）
    assert min(seen_ok.values()) > 0 and min(seen_refused.values()) > 0, (seen_ok, seen_refused)
    assert got["reads"] == {v: [6, "model.not_found"] for v in
                            ("ls", "get", "validate", "diff", "export")}, got["reads"]
