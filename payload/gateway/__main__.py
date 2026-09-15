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

    def report(snap):
        # 网段写进运行记录，`api info` / `status` / `check` 读记录而不现场重算（spec §3.4）
        log_event("info", "nets_refreshed", source=snap["nets_source"],
                  allowed=len(snap["allowed_nets"]), excluded=len(snap["excluded_ifaces"]))
        try:
            facade["supervisor.merge_record"](
                c, "api", {k: snap[k] for k in supervisor.MERGEABLE}, pid=os.getpid())
        except Exception as exc:                        # noqa: BLE001
            log_event("error", "nets_report_failed", detail=str(exc)[:200])

    nets = peers.NetView(peers.command_loader(c.bins),
                         allow_cidrs=os.environ.get("MLX_GATEWAY_ALLOW_CIDRS", ""),
                         on_change=report)
    gw = Gateway(c, facade, bind_addr=bind, token=token, backend_key=backend_key, nets=nets)
    serve(gw, host=bind, port=c.gateway_port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
