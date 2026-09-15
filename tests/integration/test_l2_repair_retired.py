"""spec §6.3：repair 的 B（旧字段）与 C（退役配置键）。不变式：其余字段逐字节不变。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + """
import stat
from mlx_server import store, ui
from mlx_server.cmd.model_reclaim import run
from mlx_server.cmd.svc import Invocation
seed("old", "org/old", 8101, config={},
     user={"type": "text", "backend": "mlx_lm", "max_tokens": 2048},
     machine={"type_evidence": "CLI_EXPLICIT", "layout": "hub"})
# Ruling L4：默认 seed() 的 machine 段已带非空 revision/size_mb，A 动作
# （只填空不覆盖）永远无字段可填。这里把 size_mb 显式设成空态哨兵 0——
# `store.rebuild_machine` 把 0 与 None/"" 同归一类「空」（`store.py` 的
# `before not in (None, "", 0)`）——让 A 对 "new" 这条记录真的有事可做。
# 选 size_mb 而非 revision：磁盘扫出来的体积在本测试的微型夹具下四舍五入
# 也是 0.0，`0 == 0.0`，dict 相等断言不受影响；revision 会被填成 "main"
# （≠ 空），会打破下面「new 逐字节不变」的断言。
seed("new", "org/new", 8102, config={}, user={"modality": "text"},
     machine={"size_mb": 0})
put_settings({"backend": "mlx_lm", "fit.reserve_pct": 30})
os.chmod(os.path.join(DATA, "settings.json"), 0o644)
before = {r.id: {"user": dict(r.user), "machine": dict(r.machine)}
          for r in store.load(ctx, store.MODELS).records}
out = run(ctx, None, Invocation("repair", None), {})
after = {r.id: {"user": dict(r.user), "machine": dict(r.machine)}
         for r in store.load(ctx, store.MODELS).records}
again = run(ctx, None, Invocation("repair", None), {})
print(json.dumps({
    "rc": out.rc, "payload": out.payload, "text": ui._repair(out.payload),
    "before": before, "after": after, "settings": store.load_settings(ctx),
    "mode": stat.S_IMODE(os.stat(os.path.join(DATA, "settings.json")).st_mode),
    "again": again.payload}))
"""


def test_repair_clears_old_fields_and_retired_keys_only(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["rc"] == 0, got
    p = got["payload"]
    assert sorted((c["id"], c["segment"], c["field"]) for c in p["cleared_fields"]) == [
        ("old", "machine", "type_evidence"), ("old", "user", "type")]
    assert sorted((r["scope"], r["key"]) for r in p["retired_keys"]) == [
        ("global", "backend"), ("old", "backend")]

    want_machine = dict(got["before"]["old"]["machine"])
    want_machine.pop("type_evidence")
    assert got["after"]["old"]["machine"] == want_machine, "B 顺带改了 machine 段其余字段"
    assert got["after"]["old"]["user"] == {"repo": "org/old", "max_tokens": 2048}
    assert got["after"]["new"] == got["before"]["new"], "没有旧字段的记录被改动了"
    assert got["settings"] == {"fit.reserve_pct": 30}
    assert got["mode"] == 0o600, f"C 动作之后 settings.json 权限是 {oct(got['mode'])}"

    for head in ("重建了", "旧字段", "退役配置键"):
        assert head in got["text"], f"repair 输出缺少「{head}」一节：\n{got['text']}"
    assert got["again"]["cleared_fields"] == [] and got["again"]["retired_keys"] == [], \
        "第二次 repair 应无事可做（幂等）"
