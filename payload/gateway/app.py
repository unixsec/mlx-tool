"""T2 · 网关装配与请求编排。

只经**五符号只读门面**调 T1（`G-DEP-03` 相等断言，多一个少一个都红）。一次请求：
`precheck`（来源分类 → 预检 → 鉴权 → Content-Type → 规范化与白名单）→ 路由（body.model 或按 id 扇出）
→ 目标端点包含性 → 入站管线（`translate.build_upstream`）→ **上游一律流式**（D22）→
客户端要流式：server 层逐帧改写直出（保活、断连即取消）；否则 `handle` 聚合成非流式响应。
"""
import json
import os
import sys
import tempfile
from urllib.parse import urlsplit

from mlx_server.wire import SseDecoder

from . import aggregate, endpoints, fetch, frames, guard, media, peers, relay, routes, translate

# `G-DEP-03` 的五符号白名单（第五个是 spec §3.4 的放行网段上报，只能写
# `supervisor.MERGEABLE` 里的键）。**相等断言** —— 网关 import 了 store 就能调
# commit，所以门面必须是白名单而不是「别乱调」的口头约定。
T1_FACADE = ("store.load", "supervisor.running",
             "supervisor.port_owner", "supervisor.snapshot", "supervisor.merge_record")

# 媒体与在途预算的出厂值（spec §4.4；与 schema.tsv 同值 —— 网关进程读环境变量，缺失时用这里）
LIMIT_DEFAULTS = {"media_max_mb": 128, "media_max_parts": 32, "inflight_max_mb": 2048}

_NO_SNAP = object()


class Gateway:
    """网关进程内状态。**日志两条流写本进程 fd 1/2**，文件由 supervisor 重定向。"""

    __slots__ = ("ctx", "facade", "view", "bind_addr", "_token", "_backend_key", "nets",
                 "_key_missing_logged", "_key_missing_had_key", "limits",
                 "budget", "media_store", "keepalive_sec", "probe_sec")

    def __init__(self, ctx, facade, bind_addr="127.0.0.1", token=None, backend_key=None,
                 nets=None, limits=None, media_root=None, keepalive_sec=None, probe_sec=None):
        self.ctx = ctx
        self.facade = facade            # {name: callable}，键集 == T1_FACADE
        self.view = routes.RouteView()
        self.bind_addr = bind_addr
        # 令牌在**装配段**注入，请求处理路径改不了它（信任边界）
        self._token = token
        self._backend_key = backend_key
        self.nets = nets
        self.limits = dict(LIMIT_DEFAULTS, **(limits or {}))
        # 在途字节预算（请求体 + 代取 + 临时文件，spec §4.4）：全网关一份，按请求的 Ticket 记账
        self.budget = media.Budget(int(self.limits["inflight_max_mb"]) * 1024 * 1024)
        if media_root is None:
            media_root = (os.path.join(ctx.root_data, "run", "media") if ctx is not None
                          else os.path.join(tempfile.gettempdir(), "mlx-gateway-media"))
        self.media_store = media.Store(media_root)
        self.keepalive_sec = relay.KEEPALIVE_SEC if keepalive_sec is None else keepalive_sec
        self.probe_sec = relay.PROBE_SEC if probe_sec is None else probe_sec
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

    def upstream_headers(self, short_id, snap=_NO_SNAP):
        """转发给后端时附加的请求头（spec §4.8）。

        **只给运行记录标了 `backend_auth` 的后端附密钥**：是否需要鉴权是启动那一刻判定的
        （运行记录快照，INV-27），这里不重新判定；mlx_lm 没有鉴权机制，不把密钥发给它。
        请求路径上传入 `prepare` 读到的**同一份** `snap`：一次请求只读一次快照，
        路由判定与鉴权头不会各自看到运行记录的不同版本。
        """
        if snap is _NO_SNAP:
            snap = self.facade["supervisor.snapshot"](self.ctx, short_id)
        if not (snap or {}).get("backend_auth"):
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
        """上游 HTTP 错误 → 客户端回复。**所有调用点都经这里**，按 `peer_class` 净化。"""
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

    def new_ticket(self):
        # type: () -> object
        """一次请求的在途字节与临时目录。**调用方在 finally 里 `close()`**。"""
        return media.Ticket(self.budget, self.media_store)

    def media_context(self, ticket):
        # type: (object) -> dict
        snap = self.nets.snapshot() if self.nets is not None else {}
        nets, own = snap.get("allowed_nets") or [], snap.get("own_addrs") or []

        def fetcher(url, peer_class, allowed_nets, max_bytes, kinds):
            # 本机自身地址随同一份快照绑进来：media 层的 fetcher 签名不变
            return self.fetch_media(url, peer_class, allowed_nets, max_bytes, kinds, own_addrs=own)
        return {"nets": nets, "limits": self.limits, "ticket": ticket, "store": self.media_store,
                "fetcher": fetcher}

    @staticmethod
    def fetch_media(url, peer_class, allowed_nets, max_bytes, kinds, own_addrs=None):
        # type: (str, str, list, int, tuple, object) -> dict
        """代取并记 `media_fetched`。**只记主机名、原因码与字节数** —— URL 的路径与查询串里常有签名，
        `FetchError.detail` 可能带异常原文，同样不进日志。"""
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        try:
            got = fetch.fetch(url, peer_class, allowed_nets, max_bytes, kinds, own_addrs=own_addrs)
        except fetch.FetchError as exc:
            log_event("warn", "media_fetched", host=host, reason=exc.reason)
            raise
        log_event("info", "media_fetched", host=host, reason="ok", bytes=len(got["data"]))
        return got

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
        调一次（复检 Important 5）。
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
        → 路由（body.model 或扇出）→ 目标 `endpoints` 包含性**。前一半全在 `precheck` 里。

        → `("reply", (status, body))` / `("fanout", [(short_id, port, snap)], upstream_path, peer_class)` /
        `("forward", port, model_arg, short_id, peer_class, upstream_path, family, snap)`。
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
            targets = []
            for sid in self.view.route_keys():
                snap = self.facade["supervisor.snapshot"](self.ctx, sid)
                if family in endpoints.endpoints_of(snap):
                    targets.append((sid, self.view.port_of(sid), snap))
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
        snap = self.facade["supervisor.snapshot"](self.ctx, short_id) or {}
        if family not in endpoints.endpoints_of(snap):
            return ("reply", translate.build_error(
                404, "endpoint_not_supported_by_backend",
                f"model {short_id!r} ({snap.get('backend') or 'mlx_lm'}) does not serve {path}"))
        model_arg = snap.get("model_arg") or short_id
        return ("forward", port, model_arg, short_id, peer_class, upstream, family, snap)

    def handle(self, method, path, headers=None, body=None, forward=None, peer="127.0.0.1",
               stream=None, client_alive=None, ticket=None):
        """一次**非流式客户端**请求的完整链路。流式客户端在 server 层，共用 `prepare` 与入站管线。

        `forward` 只服务按 id 扇出的端点；按 body 路由的端点经
        `stream(port, path, payload, on_chunk, headers=, client_alive=)`（上游一律流式），
        帧在这里聚合成非流式响应。客户端在等待期间断开 ⇒ 返回 `None`（上游已被关闭，没有人可回）。
        `ticket` 由调用方传入时归调用方关闭；否则在这里的 finally 关闭。
        """
        kind, *rest = self.prepare(path, headers, body, method=method, peer=peer)
        if kind == "reply":
            return rest[0]

        if kind == "fanout":
            if forward is None:
                return translate.build_error(503, "no_upstream", "gateway has no upstream transport")
            targets, upstream, peer_class = rest

            def call(sid, port, snap):
                hdrs = self.upstream_headers(sid, snap)
                return lambda: forward(port, upstream, body if method == "POST" else None,
                                       method=method, timeout_sec=endpoints.FANOUT_TIMEOUT_SEC,
                                       **({"headers": hdrs} if hdrs else {}))
            status, rbody, sid = endpoints.fanout([(s, call(s, p, sn)) for s, p, sn in targets])
            if 200 <= status < 300:
                return (status, translate.outbound(rbody, sid))
            if status == 404:
                return translate.build_error(404, "not_found", "no running backend knows this id")
            if status == 504:
                return translate.build_error(504, "upstream_timeout", "backends did not answer in time")
            return self.upstream_reply(status, rbody, peer_class, sid)

        port, model_arg, short_id, peer_class, upstream, family, snap = rest
        if stream is None:
            return translate.build_error(503, "no_upstream", "gateway has no upstream transport")
        own_ticket = ticket is None
        if own_ticket:
            ticket = self.new_ticket()
        try:
            try:
                up_body, _meta = translate.build_upstream(body, family, snap, peer_class, model_arg,
                                                          self.media_context(ticket))
            except (media.MediaError, translate.InboundError) as exc:
                return exc.reply()
            agg = aggregate.Aggregator(family)
            dec = SseDecoder()          # 上游分块不对齐帧边界：先拼成完整帧再喂聚合器
            bad = []

            def on_chunk(chunk):
                for frame in dec.feed(chunk):
                    if bad:
                        continue
                    try:
                        agg.feed(frame)
                    except Exception:                   # noqa: BLE001  畸形帧不得打穿工作线程
                        bad.append(True)
            kw = {}
            hdrs = self.upstream_headers(short_id, snap)
            if hdrs:
                kw["headers"] = hdrs
            if client_alive is not None:
                kw["client_alive"] = client_alive
            out = stream(port, upstream, up_body, on_chunk, **kw)
            if isinstance(out, tuple):
                return self.upstream_reply(out[0], out[1], peer_class, short_id)
            if out == "client_disconnected":
                log_event("info", "client_disconnected", model=short_id)
                return None
            if out is not True:
                # 连不上上游或流中途断开（M4：502，不再是 504；与 client_disconnected 区分）
                log_event("error", "upstream_aborted", model=short_id)
                return translate.build_error(502, "upstream_error", "backend connection failed")
            # 截断：半帧，或流结束时既没见过终止帧也没有错误帧（与流式直出同一判据，spec §4.6
            # 「已开始流式后上游中断 ⇒ sse_truncated」——客户端要不要流式不改变这件事的名字）
            pending = len(dec.pending()) if dec.pending().strip() else 0
            truncated = not bad and (pending > 0 or (not agg.done and agg.error is None))
            try:
                if bad:
                    raise aggregate.AggregateError("upstream sent a malformed frame")
                if pending:
                    raise aggregate.AggregateError("upstream stream truncated mid-frame")
                result = agg.result()
            except aggregate.AggregateError as exc:
                if truncated:
                    log_event("error", "sse_truncated", model=short_id, pending_bytes=pending)
                else:
                    log_event("error", "upstream_aborted", model=short_id)
                return aggregate_error_reply(exc, peer_class)
            return (200, frames.patch_body(result, short_id))
        finally:
            if own_ticket:
                ticket.close()


def aggregate_error_reply(exc, peer_class):
    # type: (Exception, str) -> tuple
    """聚合失败 → 502 `upstream_error`。

    `AggregateError` 的消息可能是上游错误对象 `json.dumps` 后的原文：对远端来源**先还原成结构化对象
    再净化**（字符串化后净化，`sanitize` 保留 `type` / `code` / `param` 的规则就失效了），
    然后再构造错误体。本机来源看原文。
    """
    raw = str(exc.args[0]) if exc.args else "upstream error"
    if peer_class == "LOOPBACK":
        return translate.build_error(502, "upstream_error", raw)
    try:
        obj = json.loads(raw)
    except ValueError:
        obj = raw
    if not isinstance(obj, (dict, list)):
        return translate.build_error(502, "upstream_error", translate.sanitize(raw))
    clean = translate.sanitize(obj)
    message = clean.get("message") if isinstance(clean, dict) else None
    status, body = translate.build_error(
        502, "upstream_error",
        message if isinstance(message, str) else json.dumps(clean, ensure_ascii=False))
    if isinstance(clean, dict):
        for key in ("code", "param"):
            if isinstance(clean.get(key), str):
                body["error"][key] = clean[key]
    return (status, body)


def log_event(level, event, **fields):
    """网关的两条日志。**经自己的适配器写本进程 fd 1/2，不经 ui**（BC-D-02）。"""
    line = json.dumps({"level": level, "event": event, **fields}, ensure_ascii=False)
    stream = sys.stdout if level == "info" else sys.stderr
    stream.write(line + "\n")
    # supervisor 把本进程 fd 1/2 重定向到 api.log —— 文件是块缓冲，不 flush 的话 info 事件
    # （含 http 访问日志）攒满 8 KB 才落盘，排查时看不到刚发生的请求（L3 实测）
    stream.flush()
