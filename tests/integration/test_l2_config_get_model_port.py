"""`config get/ls <模型>` 报的端口必须是 `up` 会用的那个。

坏样本（本轮真实发生）：沙箱登记 8150 的模型，`config get tiny --key=port` 报
`8100（default）`。登记端口住在记录的 **machine 段**，而 `config._layers` 只取
settings 与记录的 user 段 —— 读侧与 `svc._v_config`（`up` 的求值点）各用一份层序，
同一个键两个命令两个答案。判据取**与 `up` 的求值结果对账**，不是硬编码 8150。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + """
from mlx_server import backendarg
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation, run

put_caps(["mlx_lm", "mlx_vlm", "mlx"])
seed("tiny", "org/tiny", 8150, config="{}")
seed("own", "org/own", 8151, config="{}", user={"port": 8160})
res = {}
for label, settings in (("plain", {}), ("global_port", {"port": 9000})):
    put_settings(settings)
    for mid in ("tiny", "own"):
        up = run(ctx, None, Invocation("up", mid, dry_run=True),
                 {"backendarg_spec": backendarg.spec}).payload
        got = config.run(ctx, None, Invocation("get", mid, args={"key": "port"}), {})
        ls = config.run(ctx, None, Invocation("ls", mid), {})
        res[label + "/" + mid] = {
            "up_port": str(up["argv"][up["argv"].index("--port") + 1]),
            "up_src": up["sources"]["port"],
            "get": [got.payload["value"], got.payload["source"]],
            "ls": [ls.payload["values"]["port"], ls.payload["sources"]["port"]]}
print(json.dumps(res))
"""


def test_config_get_model_port_equals_what_up_uses(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    assert set(got) == {"plain/tiny", "plain/own", "global_port/tiny", "global_port/own"}
    for case, r in got.items():
        assert str(r["get"][0]) == r["up_port"], f"{case}: get 与 up 端口不一致：{r}"
        assert r["get"][1] == r["up_src"], f"{case}: 来源标签与 up 不一致：{r}"
        assert [str(r["ls"][0]), r["ls"][1]] == [str(r["get"][0]), r["get"][1]], (
            f"{case}: ls 与 get 不一致：{r}")
    # 阳性：登记端口真的不是 8100，来源是 machine / user，不是 default
    assert got["plain/tiny"]["get"] == [8150, "machine"], got["plain/tiny"]
    assert got["plain/own"]["get"] == [8160, "user"], got["plain/own"]
    # 全局 `port` 设置不得盖过模型自己的端口（`up` 也不吃它）
    assert got["global_port/tiny"]["get"] == [8150, "machine"], got["global_port/tiny"]
