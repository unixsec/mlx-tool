"""D · `info` —— 只放网关**独有的名词面**（生命周期走统一动词 `up api` / `down api`）。

**读运行记录，不现场重算**（spec §3.4）：放行网段、绑定、TLS、解释器都是网关进程自己
算出并经门面写进 `run/api.json` 的 —— `api info` 现场再跑一遍 ifconfig，
算出来的可能与网关正在用的那份不同。
"""
import ipaddress

from .. import supervisor
from ..result import Done

_LABELS = {"loopback": "本机", "wifi": "Wi-Fi", "ethernet": "有线", "vm": "虚拟机共享网络"}


def _model_ids(ctx):
    # type: (object) -> tuple
    """返回 `(可路由的, 已登记但转发不到的)`。判据与网关路由表同源（`running ∧ port_owner`）。"""
    try:
        from .. import store
        from ..result import is_unknown
        recs = store.load(ctx, store.MODELS).records
    except Exception:                              # noqa: BLE001
        return ((), ())
    routable, registered_only = [], []
    for r in recs:
        alive = supervisor.running(ctx, r.id)
        own = supervisor.port_owner(ctx, r.id)
        ok = (not is_unknown(alive) and alive.value is True
              and not is_unknown(own) and own.value is True)
        (routable if ok else registered_only).append(r.id)
    return (tuple(sorted(routable)), tuple(sorted(registered_only)))


def _urls(snap, port):
    # type: (dict, int) -> list
    """每个放行接口一组 base URL。链路本地地址不列（要带 `%接口`，客户端普遍不支持）。"""
    scheme = "https" if snap.get("tls") else "http"
    bind = snap.get("bind") or "0.0.0.0"
    out, seen = [], set()
    for n in snap.get("allowed_nets") or []:
        addr = n.get("addr")
        if not addr or n.get("kind") not in _LABELS:
            continue
        ip = ipaddress.ip_address(addr)
        if ip.is_link_local or addr in seen:
            continue
        if bind not in ("0.0.0.0", "::") and addr != bind and not ip.is_loopback:
            continue                               # 绑定了具体地址 ⇒ 只有它可达
        seen.add(addr)
        host = f"[{addr}]" if ip.version == 6 else addr
        out.append({"label": _LABELS[n["kind"]], "iface": n.get("iface"),
                    "openai": f"{scheme}://{host}:{port}/v1",
                    "anthropic": f"{scheme}://{host}:{port}"})
    order = {"本机": 0, "Wi-Fi": 1, "有线": 2, "虚拟机共享网络": 3}
    # 本机在前，其后按常用程度；同类里 IPv4 在前（客户端对 IPv6 字面量地址的支持参差不齐）
    out.sort(key=lambda u: (order[u["label"]], "[" in u["openai"]))
    return out


def run(ctx, stack, inv, deps=None):
    from .. import route, store
    snap = supervisor.snapshot(ctx, "api") or {}
    running = supervisor.running(ctx, "api")
    settings_error = None
    try:
        settings = store.load_settings(ctx)
    except store.StoreParseError as e:
        # **窄捕获**：此前这里吞了一切异常，settings.json 手滑写坏后
        # `token.configured` 塌成 False——网关明明带着令牌好好跑着，
        # 这一屏却说「未配置」，还指去 `mlx bootstrap keys`（那条命令
        # 在同一份坏文件上会一样读不出来，把用户支到死路上）。
        # 读不出的原因**要说出来**，不是静默假装「没配」。
        settings_error = str(e)
        settings = None
    port = snap.get("port") or ctx.gateway_port
    urls = _urls(snap, port) if snap else []
    remote = [u for u in urls if u["label"] != "本机"]
    v4_remote = [u for u in remote if "[" not in u["openai"]]
    base = ((v4_remote or remote or urls)[0]["openai"] if urls
            else f"http://127.0.0.1:{ctx.gateway_port}/v1")
    ks = route.gateway_key_state(snap, settings, getattr(ctx, "gateway_token_env", ""))
    routable, registered_only = _model_ids(ctx)
    return Done({
        "kind": "api",
        "running": None if running.is_unknown() else running.value,
        "undeterminable": running.kind if running.is_unknown() else None,
        "port": port, "pid": snap.get("pid"),
        "base_url": base, "urls": urls,
        "bind": snap.get("bind") or (settings or {}).get("api.bind") or "0.0.0.0",
        "tls": bool(snap.get("tls")),
        "interpreter": snap.get("interpreter"),
        "interpreter_version": snap.get("interpreter_version"),
        "tls_max": snap.get("tls_max"),
        # `settings is None`（读不出）时 `configured` 是**未知**，不是 False——
        # 「没配」与「读不出所以不知道」是两件事，渲染层要能分辨（见 render_meta._api）。
        "token": {"configured": None if settings is None else bool(settings.get("api.token")),
                  "source": snap.get("token_source"), "state": ks["token_state"]},
        "settings_error": settings_error,
        "allowed_nets": snap.get("allowed_nets") or [],
        "excluded_ifaces": snap.get("excluded_ifaces") or [],
        "nets_source": snap.get("nets_source"),
        "nets_computed_at": snap.get("nets_computed_at"),
        "nets_error": snap.get("nets_error") or "",
        # 「可用」= 网关真的转发得到的那些，与 `/v1/models` 同一判据
        "models": list(routable), "registered_only": list(registered_only),
        "plaintext_warning": bool(remote) and not snap.get("tls"),
        "note_key": "api.exposure_note",
    })
