"""T2 · 网关装配与生命周期。

只经**五符号只读门面**调 T1（`G-DEP-03` 相等断言，多一个少一个都红）。
"""
import json
import sys

from . import endpoints, guard, peers, routes, translate

# `G-DEP-03` 的五符号白名单（第五个是 spec §3.4 的放行网段上报，只能写
# `supervisor.MERGEABLE` 里的键）。**相等断言** —— 网关 import 了 store 就能调
# commit，所以门面必须是白名单而不是「别乱调」的口头约定。
T1_FACADE = ("store.load", "supervisor.running",
             "supervisor.port_owner", "supervisor.snapshot", "supervisor.merge_record")


class Gateway:
    """网关进程内状态。**日志两条流写本进程 fd 1/2**，文件由 supervisor 重定向。"""

    __slots__ = ("ctx", "facade", "view", "bind_addr", "_token", "_backend_key", "nets",
                 "_key_missing_logged", "_key_missing_had_key")

    def __init__(self, ctx, facade, bind_addr="127.0.0.1", token=None, backend_key=None,
                 nets=None):
        self.ctx = ctx
        self.facade = facade            # {name: callable}，键集 == T1_FACADE
        self.view = routes.RouteView()
        self.bind_addr = bind_addr
        # 令牌在**装配段**注入，请求处理路径改不了它（信任边界）
        self._token = token
        self._backend_key = backend_key
        self.nets = nets
        # `backend_key_missing` 去重：按「模型 + 当前是否有密钥」——密钥状态翻转
        # （比如运行中改配置后重装配）就重新起算，翻转前的沉默不会漏掉新一轮缺钥匙
        self._key_missing_logged = set()
        self._key_missing_had_key = bool(backend_key)
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
        have_key = bool(self._backend_key)
        if have_key != self._key_missing_had_key:
            self._key_missing_logged.clear()
            self._key_missing_had_key = have_key
        if not have_key:
            if short_id not in self._key_missing_logged:
                log_event("error", "backend_key_missing", model=short_id)
                self._key_missing_logged.add(short_id)
            return {}
        return {"Authorization": "Bearer " + self._backend_key}

    def upstream_reply(self, status, body, peer_class, short_id):
        # type: (int, object, str, str) -> tuple
        if status == 401:
            # 网关带的后端密钥被拒 —— 多半是改了 backend.api_key 却没重启网关或模型
            log_event("error", "backend_auth_failed", model=short_id)
        return translate.upstream_error(status, body, peer_class)

    def healthz(self):
        # 免鉴权，所以**只回状态**：绑定地址等配置不在这里（spec §4.1）
        return (200, {"routes": self.view.state, "ts": self.view.refreshed_at})

    def classify(self, peer):
        # type: (str) -> object
        """来源分类。没有网段视图（测试或装配前）⇒ 只认回环，不放行任何外部地址。"""
        if self.nets is not None:
            return self.nets.classify(peer)
        ip = peers.normalize_peer(peer)
        return "LOOPBACK" if ip is not None and ip.is_loopback else None

    def precheck(self, method, path, headers, peer):
        # type: (str, str, dict, str) -> tuple
        """**来源分类 → 预检短路 → 鉴权 → Content-Type → 路径规范化与白名单**。

        → `(peer_class, 规范化路径, 查询串, endpoints.match 命中 | None, 回复 | None)`。

        server 层在**读请求体之前**调它：未鉴权、来源不被放行、方法+路径不在白名单里
        （比如 `/healthz` 只挂 GET，`POST /healthz` 在这里就该 404）的请求都不值得读最多
        256 MB 的请求体——白名单判定也搬进来了，否则 `do_POST` 得先读完体才知道该 404
        （复检 Critical 2）。`prepare` 复用同一份实现，直接消费它给出的 `hit`，
        不再自己另调一遍 `endpoints.match`。

        **只接收原始路径**（不接受已规范化过的路径）：`normalize` 在整条链路上只在这里
        调一次（复检 Important 5）——两个调用方（server 层直调、`prepare` 内部）现在喂给它
        的都是请求行上的原始路径，不会有「一个调用方喂已规范化路径、另一个喂原始路径」
        这种形状分歧。
        """
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        path, query = endpoints.normalize(path)
        peer_class = self.classify(peer)
        blocked = guard.peer_guard(peer_class)
        if blocked:
            log_event("warn", "peer_denied", addr=str(peer))
            return peer_class, path, query, None, blocked
        for check in (lambda: guard.preflight(method, headers),
                      lambda: guard.auth_guard(path, headers, self._token),
                      lambda: guard.content_type_guard(method, headers)):
            out = check()
            if out:
                return peer_class, path, query, None, out
        hit = endpoints.match(method, path)
        if hit is None:
            return peer_class, path, query, None, translate.build_error(
                404, "endpoint_not_supported", f"{method} {path} is not served by this gateway")
        return peer_class, path, query, hit, None

    def prepare(self, path, headers=None, body=None, method="POST", peer="127.0.0.1"):
        """请求预处理的**唯一实现**（非流式 `handle` 与 server 层流式路径共用）。

        顺序固定（spec §4.2）：**来源分类 → 预检短路 → 鉴权 → Content-Type → 路径规范化与白名单
        → 路由（body.model 或扇出）→ 目标 `endpoints` 包含性**。前一半（到白名单为止）全在
        `precheck` 里，这里只消费它的结果。
        """
        peer_class, path, query, hit, early = self.precheck(method, path, headers, peer)
        if early:
            return ("reply", early)
        family, how = hit
        if how == "local":
            return ("reply", self.healthz() if path == "/healthz"
                    else translate.list_models(self.view))
        # 查询串**只给扇出端点**（`input_items` 的分页参数在这里）。按 body 路由的端点丢掉它：
        # mlx_lm 按路径**精确匹配**（`/v1/messages?beta=true` 这类客户端默认带的查询串会让它 404），
        # 而这些端点的参数全在请求体里
        upstream = path + ("?" + query if query and how == "fanout" else "")
        if how == "fanout":
            targets = [(sid, self.view.port_of(sid)) for sid in self.view.route_keys()
                       if family in endpoints.endpoints_of(
                           self.facade["supervisor.snapshot"](self.ctx, sid))]
            return ("fanout", targets, upstream, peer_class)

        blocked = guard.route_guard(path, body, self.view.route_keys())
        if blocked:
            return ("reply", blocked)
        short_id = (body or {}).get("model")
        port = self.view.port_of(short_id)
        if port is None:
            return ("reply", translate.build_error(503, "route_lost",
                                                   f"model {short_id!r} is no longer routable"))
        # INV-27：对正在跑的服务，`model_arg` 与能力只从运行记录快照读，不调 `spec()`
        snap = self.facade["supervisor.snapshot"](self.ctx, short_id)
        if family not in endpoints.endpoints_of(snap):
            return ("reply", translate.build_error(
                404, "endpoint_not_supported_by_backend",
                f"model {short_id!r} ({(snap or {}).get('backend') or 'mlx_lm'}) does not serve {path}"))
        model_arg = (snap or {}).get("model_arg") or short_id
        return ("forward", port, model_arg, short_id, peer_class, upstream)

    def handle(self, method, path, headers=None, body=None, forward=None, peer="127.0.0.1"):
        """一次**非流式**请求的完整链路。流式在 server 层，见 `prepare`。"""
        kind, *rest = self.prepare(path, headers, body, method=method, peer=peer)
        if kind == "reply":
            return rest[0]
        if forward is None:
            return translate.build_error(503, "no_upstream", "gateway has no upstream transport")

        if kind == "fanout":
            targets, upstream, _peer_class = rest

            def call(sid, port):
                hdrs = self.upstream_headers(sid)
                return lambda: forward(port, upstream, body if method == "POST" else None,
                                       method=method, timeout_sec=endpoints.FANOUT_TIMEOUT_SEC,
                                       **({"headers": hdrs} if hdrs else {}))
            status, rbody, sid = endpoints.fanout([(s, call(s, p)) for s, p in targets])
            if 200 <= status < 300:
                return (status, translate.outbound(rbody, sid))
            if status == 404:
                return translate.build_error(404, "not_found", "no running backend knows this id")
            if status == 504:
                return translate.build_error(504, "upstream_timeout", "backends did not answer in time")
            return self.upstream_reply(status, rbody, _peer_class, sid)

        port, model_arg, short_id, peer_class, upstream = rest
        hdrs = self.upstream_headers(short_id)
        # 只在需要时传 headers —— 不需要鉴权的转发，调用形态与此前逐字一致
        out = forward(port, upstream, translate.inbound(body, model_arg),
                      **({"headers": hdrs} if hdrs else {}))
        if out is None:
            return translate.build_error(504, "upstream_timeout", "backend did not answer in time")
        status, rbody = out
        if status >= 400:
            return self.upstream_reply(status, rbody, peer_class, short_id)
        return (status, translate.outbound(rbody, short_id))


def log_event(level, event, **fields):
    """网关的两条日志。**经自己的适配器写本进程 fd 1/2，不经 ui**（BC-D-02）。"""
    line = json.dumps({"level": level, "event": event, **fields}, ensure_ascii=False)
    (sys.stdout if level == "info" else sys.stderr).write(line + "\n")
