"""T2 · 路由表（**进程内模块**，不承 HTTP 端点）。

路由键集是 `/v1/models` 与转发共用的**唯一真源** —— 两个端点各自派生
会让「列表里能看见、发过去用不了」（`R-API-21` 三向一致的反例）。
"""
import time


class RouteView:
    """只读路由视图。`state ∈ {building, fresh, stale}`。"""

    __slots__ = ("_routes", "_state", "_refreshed_at")

    def __init__(self):
        self._routes = {}          # short_id → {"port": int, "owner": bool}
        self._state = "building"
        self._refreshed_at = 0.0

    @property
    def state(self):
        return self._state

    @property
    def refreshed_at(self):
        return self._refreshed_at

    def route_keys(self):
        """路由键集。`/v1/models` 与转发都读它。"""
        return sorted(self._routes)

    def port_of(self, model_id):
        r = self._routes.get(model_id)
        return None if r is None else r["port"]

    def refresh(self, records, running_of, owner_of=None, now=None):
        """后台刷新。**两道合取**：`running == Ok(True)` ∧ `port_owner == Ok(True)`。

        `Unknown` 两处都不进路由表 —— 转发到一个所有权不可证的端口
        会把请求发给别人的进程。这是 `R-API-28` 的 P0 安全判定。

        **注意方向与 `may_kill` 相反**：那里 `port_owner != Ok(False)`，
        `Unknown` 落在放行一侧（被保护对象是「不要留 28GB 孤儿进程」）；
        这里 `Unknown` 落在拒绝一侧（被保护对象是「不要把请求发给别人的进程」）。
        两个方向相反不是不一致，是 MR-1 的直接后果（`03-seq-02.md:703`）。

        `owner_of` 此前**根本没有这个参数** —— `supervisor.port_owner` 登记在
        网关的四符号门面白名单里却零调用点，等于这道安全判定从未生效。
        证据原语是 `probe.listener_pid`（裁决 A6：lsof，离线、不打 HTTP）。
        缺省 `None` 时跳过该道校验，仅为不强迫每个调用方都提供它。
        """
        fresh = {}
        for r in records:
            port = r.machine.get("port")
            if port is None:
                continue
            st = running_of(r.id)
            if st.is_unknown() or st.value is not True:
                continue
            if owner_of is not None:
                own = owner_of(r.id)
                if own.is_unknown() or own.value is not True:
                    continue
            fresh[r.id] = {"port": port}
        self._routes = fresh
        self._state = "fresh"
        self._refreshed_at = now() if now else time.time()
        return self
