"""T2 · 网关装配与生命周期。

只经**四符号只读门面**调 T1（`G-DEP-03` 相等断言，多一个少一个都红）。
"""
import json
import sys

from . import guard, routes, translate

# `G-DEP-03` 的四符号白名单。**相等断言** —— 网关 import 了 store 就能调 commit，
# 所以门面必须是白名单而不是「别乱调」的口头约定。
T1_FACADE = ("store.load", "supervisor.running",
             "supervisor.port_owner", "supervisor.snapshot")


class Gateway:
    """网关进程内状态。**日志两条流写本进程 fd 1/2**，文件由 supervisor 重定向。"""

    __slots__ = ("ctx", "facade", "view", "bind_addr", "_token", "_backend_key")

    def __init__(self, ctx, facade, bind_addr="127.0.0.1", token=None, backend_key=None):
        self.ctx = ctx
        self.facade = facade            # {name: callable}，键集 == T1_FACADE
        self.view = routes.RouteView()
        self.bind_addr = bind_addr
        # 令牌在**装配段**注入，请求处理路径改不了它（信任边界）
        self._token = token
        self._backend_key = backend_key
        missing = set(T1_FACADE) - set(facade)
        extra = set(facade) - set(T1_FACADE)
        if missing or extra:
            raise ValueError(
                f"T1 门面必须**恰好**是 {list(T1_FACADE)}（G-DEP-03 相等断言）；"
                f"缺 {sorted(missing)}，多 {sorted(extra)}"
            )

    def refresh_routes(self):
        """后台刷新路由表。`running == Ok(True)` ∧ `port_owner == Ok(True)`。

        第二道此前没接 —— `supervisor.port_owner` 在 `T1_FACADE` 白名单里
        零调用点，于是端口易主之后网关照转不误。
        """
        res = self.facade["store.load"](self.ctx)
        self.view.refresh(
            res.records,
            lambda sid: self.facade["supervisor.running"](self.ctx, sid),
            owner_of=lambda sid: self.facade["supervisor.port_owner"](self.ctx, sid))
        return self.view

    def upstream_headers(self, short_id):
        """转发给后端时附加的请求头（spec §4.8）。

        **只给运行记录标了 `backend_auth` 的后端附密钥**：是否需要鉴权是启动那一刻判定的
        （运行记录快照，INV-27），这里不重新判定；mlx_lm 没有鉴权机制，不把密钥发给它。
        """
        snap = self.facade["supervisor.snapshot"](self.ctx, short_id) or {}
        if not snap.get("backend_auth"):
            return {}
        if not self._backend_key:
            log_event("error", "backend_key_missing", model=short_id)
            return {}
        return {"Authorization": "Bearer " + self._backend_key}

    def healthz(self):
        return (200, {"routes_state": self.view.state,
                      "refreshed_at": self.view.refreshed_at,
                      "bind": self.bind_addr})

    def prepare(self, path, headers=None, body=None):
        """两道 guard + 端点分派 + 路由解析的**唯一实现**。

        返回 `("reply", (status, body))` —— 直接回给客户端；
        或 `("forward", port, model_arg, short_id)` —— 该转发给后端。

        `handle`（非流式）与 server 层的**流式**路径共用它。
        分成两处写的话，流式路径就会长出第二套鉴权与路由判定 ——
        而这个网关的第一个 auth-bypass 正是「两个豁免集合并成一个」造成的。
        """
        headers = {k.lower(): v for k, v in (headers or {}).items()}

        blocked = guard.source_guard(path, headers, self.bind_addr, self._token)
        if blocked:
            return ("reply", blocked)
        blocked = guard.route_guard(path, body, self.view.route_keys())
        if blocked:
            return ("reply", blocked)

        if path == "/healthz":
            return ("reply", self.healthz())
        if path == "/v1/models":
            return ("reply", translate.list_models(self.view))

        short_id = (body or {}).get("model")
        port = self.view.port_of(short_id)
        if port is None:
            return ("reply", translate.build_error(503, "gateway.route_lost"))

        # INV-27：对正在跑的服务，`model_arg` 只从运行记录快照读，不调 `spec()`
        snap = self.facade["supervisor.snapshot"](self.ctx, short_id)
        model_arg = (snap or {}).get("model_arg") or short_id
        return ("forward", port, model_arg, short_id)

    def handle(self, method, path, headers=None, body=None, forward=None):
        """一次**非流式**请求的完整链路。流式在 server 层，见 `prepare`。"""
        kind, *rest = self.prepare(path, headers, body)
        if kind == "reply":
            return rest[0]
        port, model_arg, short_id = rest

        if forward is None:
            return translate.build_error(503, "gateway.no_upstream")
        hdrs = self.upstream_headers(short_id)
        # 只在需要时传 headers —— 不需要鉴权的转发，调用形态与此前逐字一致
        out = forward(port, path, translate.inbound(body, model_arg),
                      **({"headers": hdrs} if hdrs else {}))
        if out is None:
            return translate.build_error(504, "gateway.upstream_timeout")
        status, rbody = out
        if status >= 400:
            # **错误响应体不透传** —— 上游的内部信息不回给客户端
            return translate.build_error(502 if status >= 500 else status,
                                         "gateway.upstream_error")
        return (status, translate.outbound(rbody, short_id))


def log_event(level, event, **fields):
    """网关的两条日志。**经自己的适配器写本进程 fd 1/2，不经 ui**（BC-D-02）。"""
    line = json.dumps({"level": level, "event": event, **fields}, ensure_ascii=False)
    (sys.stdout if level == "info" else sys.stderr).write(line + "\n")
