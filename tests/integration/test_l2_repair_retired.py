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


# Fix round 1（controller ruling）：`Failed.committed` 只能列**失败点之前已经
# 落地**的资源，绝不能列刚失败的那一个——`atomic.replace` 全有全无，抛异常时
# 临时文件已被清掉，那次写什么都没落地。用一个假 `store.commit` 在第 N 次调用
# 时抛异常，验证 `committed` 既不含失败的那个资源、又含它之前真落地的资源。
#
# 本测试世界里一次 repair() 的 `store.commit` 调用顺序（`models.jsonl` 按 id
# 排过序，"new" < "old"）：
#   #1 A 动作重建 "new"（size_mb 空态哨兵触发）
#   #2 B 动作清理 "old"（旧字段 + 退役键）
#   #3 C 动作清理 settings.json（退役键 backend）
PROG_FAIL = WORLD + """
from mlx_server import store
from mlx_server.cmd.model_reclaim import run
from mlx_server.cmd.svc import Invocation
seed("old", "org/old", 8101, config={},
     user={"type": "text", "backend": "mlx_lm", "max_tokens": 2048},
     machine={"type_evidence": "CLI_EXPLICIT", "layout": "hub"})
seed("new", "org/new", 8102, config={}, user={"modality": "text"},
     machine={"size_mb": 0})
put_settings({"backend": "mlx_lm", "fit.reserve_pct": 30})

_orig_commit = store.commit
_calls = {"n": 0}
def _fake_commit(ctx, target, tx, stack=None):
    _calls["n"] += 1
    if _calls["n"] == @FAIL_AT@:
        raise RuntimeError("boom-injected")
    return _orig_commit(ctx, target, tx, stack)
store.commit = _fake_commit

out = run(ctx, None, Invocation("repair", None), {})
print(json.dumps({"rc": out.rc, "msg_key": getattr(out, "msg_key", None),
                  "committed": getattr(out, "committed", None)}))
"""


def test_repair_failed_committed_excludes_the_failing_write(payload, sys_py):
    # 第 2 次 commit（B 动作清理 "old"）失败：#1（A 重建 "new"）之前已经落地，
    # 必须出现在 committed 里；刚失败的 "old" 绝不能出现。
    got = run_floor(payload, sys_py, PROG_FAIL, FAIL_AT=2)
    assert got["rc"] == 1, got
    assert got["msg_key"] == "repair.commit_failed", got
    assert got["committed"] == ["models.jsonl:new"], got
    assert "models.jsonl:old" not in got["committed"], \
        "committed 里出现了刚失败、其实没落地的资源"


def test_repair_failed_committed_empty_when_first_write_fails(payload, sys_py):
    # 第 1 次 commit（A 动作重建 "new" 本身）就失败：此前什么都没落地。
    got = run_floor(payload, sys_py, PROG_FAIL, FAIL_AT=1)
    assert got["rc"] == 1, got
    assert got["msg_key"] == "repair.commit_failed", got
    assert got["committed"] == [], got
