"""网关的路由表每 5 秒后台刷新一次：`mlx up <模型>` 之后的几秒里，请求会拿到 404 model_not_found
（2026-10-31 真机验收复现）。路由不中时当场刷新一次再判，限频每秒一次。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from gateway.app import Gateway
from mlx_server.result import Ok
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("qwen", 8100)]
up = {"qwen": False}
calls = []
def running(c, s):
    calls.append(s)
    return Ok(up[s])
facade = {"store.load": lambda c: Res(), "supervisor.running": running,
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: {"model_arg": s, "endpoints": ["chat"]},
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
g = Gateway(None, facade, token="t" * 43); g.refresh_routes()
H = {"authorization": "Bearer " + "t" * 43, "content-type": "application/json"}
B = {"model": "qwen", "messages": [{"role": "user", "content": "x"}]}
up["qwen"] = True                       # 模型刚起来，后台刷新还没轮到
first = g.prepare("/v1/chat/completions", H, B)
n_after_first = len(calls)
miss1 = g.prepare("/v1/chat/completions", H, dict(B, model="ghost"))
miss2 = g.prepare("/v1/chat/completions", H, dict(B, model="ghost"))
print(json.dumps({"first": first[0], "first_body": str(first[1])[:200], "miss": [miss1[0], miss1[1][0], miss2[1][0]],
                  "refreshes_for_two_misses": len(calls) - n_after_first}))
"""


def test_route_miss_refreshes_once_then_rechecks(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["first"] == "forward", f"刚起来的模型应当场刷新后命中：{got}"
    assert got["miss"] == ["reply", 404, 404], got
    assert got["refreshes_for_two_misses"] <= 1, f"连续的不中请求每秒最多触发一次刷新：{got}"
