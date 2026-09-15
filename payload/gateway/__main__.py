"""`python3 -m gateway` —— 网关进程入口。

由 `supervisor` 以 `mlx up api` 起，argv 来自 `backendarg.spec("api", ...)`。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "lib"))

from mlx_server import ctx as ctx_mod  # noqa: E402
from mlx_server import store, supervisor  # noqa: E402

from . import peers  # noqa: E402
from .app import Gateway, log_event  # noqa: E402
from .server import serve  # noqa: E402


def make_reporter(facade, ctx, pid=None):
    # type: (dict, object, object) -> object
    """放行网段上报（spec §3.4）。→ 交给 `NetView(on_change=...)` 的回调。

    `merge_record` 返回 `False`（记录缺失 / pid 不属于本进程）不是异常，只是
    「这次没送达」——网关刚起、`mlx up api` 还没写完运行记录时是常见的一次性
    竞态（`cmd/svc.py` 在 `spawn()` 返回后才写记录）。`NetView` 会在下一次
    `tick()`（后台刷新线程每 5 s 一次）重发同一份快照，不必等满 30 s 的重算
    周期。这里只管日志：**成功才记 `nets_refreshed`**（此前不论送没送达都记，
    api.log 会显示「报过了」而运行记录其实没变）；一直没送达时只记一次
    `nets_report_skipped`（不刷屏），一旦送达就把「已经记过」的状态清掉，
    这样下一段新的失败还会再记一次。
    """
    state = {"skipped_logged": False}

    def report(snap):
        try:
            delivered = facade["supervisor.merge_record"](
                ctx, "api", {k: snap[k] for k in supervisor.MERGEABLE},
                pid=os.getpid() if pid is None else pid)
        except Exception as exc:                        # noqa: BLE001
            log_event("error", "nets_report_failed", detail=str(exc)[:200])
            return False
        if delivered:
            # 网段写进运行记录，`api info` / `status` / `check` 读记录而不现场重算（spec §3.4）
            log_event("info", "nets_refreshed", source=snap["nets_source"],
                      allowed=len(snap["allowed_nets"]), excluded=len(snap["excluded_ifaces"]))
            state["skipped_logged"] = False
        elif not state["skipped_logged"]:
            log_event("error", "nets_report_skipped", reason="record_missing_or_pid_mismatch")
            state["skipped_logged"] = True
        return delivered
    return report


def main(argv):
    share = os.environ.get("MLX_SHARE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "share")
    c = ctx_mod.build(share)

    # **五符号门面**（G-DEP-03 相等断言）——网关只经它们碰 T1
    facade = {
        "store.load": lambda cx: store.load(cx, store.MODELS),
        "supervisor.running": supervisor.running,
        "supervisor.port_owner": supervisor.port_owner,
        "supervisor.snapshot": supervisor.snapshot,
        "supervisor.merge_record": supervisor.merge_record,
    }
    bind = os.environ.get("MLX_GATEWAY_BIND", "127.0.0.1")
    token = os.environ.get("MLX_GATEWAY_TOKEN") or None
    backend_key = os.environ.get("MLX_BACKEND_API_KEY") or None

    nets = peers.NetView(peers.command_loader(c.bins),
                         allow_cidrs=os.environ.get("MLX_GATEWAY_ALLOW_CIDRS", ""),
                         on_change=make_reporter(facade, c))
    gw = Gateway(c, facade, bind_addr=bind, token=token, backend_key=backend_key, nets=nets)
    serve(gw, host=bind, port=c.gateway_port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
