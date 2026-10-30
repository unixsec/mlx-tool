"""B4：`api.inflight_max_mb` ≤ `api.media_max_mb` 时每次代取都先按 media_max_mb 整额预留、必然失败，
所有代取（本机来源也一样）都回 503，看上去像网关一直很忙。两键各自的值域都合法，以前没有跨键校验。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
put_settings({"api.token": "T" * 43})
def cset(k, v):
    r = config.run(ctx, None, Invocation("set", k, args={"key": k, "value": str(v)}), {})
    return [r.rc, getattr(r, "msg_key", None)]
out = {"media_eq": cset("api.media_max_mb", 2048),          # inflight 出厂 2048 ⇒ 相等也不行
       "inflight_lt": cset("api.inflight_max_mb", 100),     # media 出厂 128
       "ok": cset("api.media_max_mb", 512)}
put_settings({"api.token": "T" * 43, "api.inflight_max_mb": 64})   # 手改进去的
v = config.run(ctx, None, Invocation("validate", "api"), {})
out["validate"] = [v.rc, [list(r) for r in v.payload["rows"] if r[0] == "api.inflight_max_mb"]]
print(json.dumps(out, ensure_ascii=False))
"""


def test_inflight_must_exceed_media_max(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    assert got["media_eq"] == [6, "config.invalid"], got
    assert got["inflight_lt"] == [6, "config.invalid"], got
    assert got["ok"][0] == 0, got
    rc, rows = got["validate"]
    assert rc != 0 and rows and "media_max_mb" in str(rows), f"validate 应标出跨键冲突：{got}"
