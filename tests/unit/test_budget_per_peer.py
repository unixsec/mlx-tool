"""在途预算按来源分额：一个远端来源最多占 `peer_share`，被 408 过的来源 30 秒内份额减半。
本机来源只受总额限制（与连接名额同一口径）。此前单个持令牌者声明大体不发、408 后立刻重连，
就能让预算一直满着。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from gateway import media as M
from gateway import peers as P
MB = 1024 * 1024
b = M.Budget(1000 * MB, peer_share=250 * MB)
out = {}
out["a1"] = b.reserve(200 * MB, "10.0.0.1")
out["a2"] = b.reserve(100 * MB, "10.0.0.1")          # 超出该来源份额
out["b1"] = b.reserve(200 * MB, "10.0.0.2")          # 另一个来源不受影响
out["local"] = b.reserve(400 * MB, None)             # 本机只受总额限制（超过远端份额也行）
out["total"] = b.reserve(300 * MB, None)             # 总额不够（200+200+400+300 > 1000）
b.release(200 * MB, "10.0.0.1")
b.strike("10.0.0.1")
out["after_strike"] = [b.reserve(130 * MB, "10.0.0.1"), b.reserve(120 * MB, "10.0.0.1")]
out["keys"] = [P.conn_key("::ffff:10.0.0.1"), P.conn_key("127.0.0.1"),
               P.conn_key("2001:db8::1"), P.conn_key("2001:db8::2")]
t = M.Ticket(b, None, key="10.0.0.3")
t.charge(100 * MB); t.close()
out["ticket_released"] = b.reserve(250 * MB, "10.0.0.3")
print(json.dumps(out))
"""


def test_inflight_budget_is_shared_per_remote_peer(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["a1"] is True and got["a2"] is False, got
    assert got["b1"] is True and got["local"] is True and got["total"] is False, got
    assert got["after_strike"] == [False, True], f"被 408 过的来源份额减半（125 MB）：{got}"
    assert got["keys"][0] == ["10.0.0.1", False] and got["keys"][1] == ["127.0.0.1", True], got
    assert got["keys"][2] == got["keys"][3] == ["2001:db8::/64", False], got
    assert got["ticket_released"] is True, got
