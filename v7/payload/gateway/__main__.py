"""`python3 -m gateway` —— 网关进程入口。

由 `supervisor` 以 `mlx up api` 起，argv 来自 `backendarg.spec("api", ...)`。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "lib"))

from mlx_server import ctx as ctx_mod  # noqa: E402
from mlx_server import store, supervisor  # noqa: E402

from .app import Gateway  # noqa: E402
from .server import serve  # noqa: E402


def main(argv):
    share = os.environ.get("MLX_SHARE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "share")
    c = ctx_mod.build(share)

    # **四符号只读门面**（G-DEP-03 相等断言）——网关只经它们碰 T1
    facade = {
        "store.load": lambda cx: store.load(cx, store.MODELS),
        "supervisor.running": supervisor.running,
        "supervisor.port_owner": supervisor.port_owner,
        "supervisor.snapshot": supervisor.snapshot,
    }
    bind = os.environ.get("MLX_GATEWAY_BIND", "127.0.0.1")
    token = os.environ.get("MLX_GATEWAY_TOKEN") or None
    gw = Gateway(c, facade, bind_addr=bind, token=token)
    serve(gw, host=bind, port=c.gateway_port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
