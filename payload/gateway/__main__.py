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
from .server import DEFAULT_MAX_BODY_MB, serve, tls_context  # noqa: E402


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


def _int_env(key, default):
    # type: (str, int) -> int
    """读一个整数环境变量；缺失或非法用出厂值并记日志（spec §4.8）。"""
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        log_event("error", "bad_env", key=key)
        return default


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
    if not token:
        # D12：没有令牌就不起。`mlx up api` 在校验段已经拒绝；
        # 这里挡的是绕过 `mlx` 直接 `python3 -m gateway` 的启动
        log_event("error", "token_missing", detail="MLX_GATEWAY_TOKEN 为空，网关拒绝启动")
        return 6
    backend_key = os.environ.get("MLX_BACKEND_API_KEY") or None

    inflight_mb = _int_env("MLX_GATEWAY_INFLIGHT_MAX_MB", 2048)
    media_mb = _int_env("MLX_GATEWAY_MEDIA_MAX_MB", 128)
    if inflight_mb <= media_mb:
        # 绕过 mlx 直接起网关、或环境变量覆盖了配置时的兜底：这个组合下所有代取必然 503
        log_event("error", "config_invalid",
                  detail=f"MLX_GATEWAY_INFLIGHT_MAX_MB={inflight_mb} 必须大于 MLX_GATEWAY_MEDIA_MAX_MB={media_mb}")
        return 6
    nets = peers.NetView(peers.command_loader(c.bins),
                         allow_cidrs=os.environ.get("MLX_GATEWAY_ALLOW_CIDRS", ""),
                         allow_ifaces=os.environ.get("MLX_GATEWAY_ALLOW_IFACES", ""),
                         on_change=make_reporter(facade, c))
    gw = Gateway(c, facade, bind_addr=bind, token=token, backend_key=backend_key, nets=nets,
                 limits={"media_max_mb": media_mb,
                         "media_max_parts": _int_env("MLX_GATEWAY_MEDIA_MAX_PARTS", 32),
                         "inflight_max_mb": inflight_mb})

    # 进程配置全部来自 `backendarg.spec("api")` 设的环境变量（spec §4.8）；缺失或非法用出厂值并记日志
    max_body_mb = _int_env("MLX_GATEWAY_MAX_BODY_MB", DEFAULT_MAX_BODY_MB)
    cert = os.environ.get("MLX_GATEWAY_TLS_CERT") or ""
    key = os.environ.get("MLX_GATEWAY_TLS_KEY") or ""
    tls = None
    if cert or key:
        if not (cert and key):
            log_event("error", "tls_incomplete", detail="证书与私钥必须同时配置")
            return 6
        try:
            tls = tls_context(cert, key)
        except ValueError as exc:
            log_event("error", "tls_unusable", detail=str(exc)[:200])
            return 6
    serve(gw, host=bind, port=c.gateway_port,
          max_body_bytes=max_body_mb * 1024 * 1024, tls_context=tls)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
