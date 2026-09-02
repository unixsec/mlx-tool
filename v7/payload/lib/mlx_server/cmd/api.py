"""D · `info` —— 只放网关**独有的名词面**（生命周期走统一动词 `up api` / `down api`）。"""
from .. import supervisor
from ..result import Done


def _model_ids(ctx):
    # type: (object) -> tuple
    """返回 `(可路由的, 已登记但转发不到的)`。

    **两个列表，因为工具里本来就有两个集合**：网关的路由表只收
    `running == Ok(True) ∧ port_owner == Ok(True)` 的服务，而注册表里
    躺着全部已登记的模型。此前这里只报后者 —— 用户从「模型」那行抄一个 id
    填进 Cherry Studio，客户端却连不上，而 `api info` 一直显示网关在跑。
    `gateway/routes.py` 开头的注释正是为这件事写的：「两个端点各自派生会让
    『列表里能看见、发过去用不了』」。判据与网关侧同源，不另立一套。
    """
    try:
        from .. import store, supervisor
        from ..result import is_unknown
    except Exception:                              # noqa: BLE001
        return ((), ())
    routable, registered_only = [], []
    try:
        recs = store.load(ctx, store.MODELS).records
    except Exception:                              # noqa: BLE001
        return ((), ())
    for r in recs:
        alive = supervisor.running(ctx, r.id)
        own = supervisor.port_owner(ctx, r.id)
        ok = (not is_unknown(alive) and alive.value is True
              and not is_unknown(own) and own.value is True)
        (routable if ok else registered_only).append(r.id)
    return (tuple(sorted(routable)), tuple(sorted(registered_only)))


def run(ctx, stack, inv, deps=None):
    snap = supervisor.snapshot(ctx, "api")
    # **报真实绑定地址**。此前写死 127.0.0.1：绑到 0.0.0.0 的网关，
    # `api info` 依然打印回环地址与那句「边界是绑定地址」的诚实陈述 ——
    # 工具对自己的暴露面说了假话，而这一屏正是用户判断暴露面的地方。
    from .. import schema, store
    _bind = store.load_settings(ctx).get("api.bind") or schema.DEFAULTS["api.bind"]
    snap = supervisor.snapshot(ctx, "api") or {}
    bind = snap.get("bind") or _bind
    base = f"http://{bind}:{ctx.gateway_port}/v1"
    _routable, _registered_only = _model_ids(ctx)
    running = supervisor.running(ctx, "api")
    return Done({
        "base_url": base,
        "running": None if running.is_unknown() else running.value,
        "undeterminable": running.kind if running.is_unknown() else None,
        "port": ctx.gateway_port,
        "pid": (snap or {}).get("pid"),
        # 诚实陈述（R-API-26）：同机场景下模型端口本就在回环上无鉴权监听，
        # 网关令牌提供零额外阻挡，真正的边界是绑定地址。
        "kind": "api",
        # 附录 A 2829 要求三项：base url + **可用 id 列表** + 诚实陈述。
        # 「可用」= 网关真的转发得到的那些，与 `/v1/models` 同一判据。
        "models": list(_routable),
        "registered_only": list(_registered_only),
        "honest_note_key": "api.honest_no_boundary",
    })
