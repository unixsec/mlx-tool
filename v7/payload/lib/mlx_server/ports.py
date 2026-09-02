"""C 层 · 端口空间。分配的唯一入口 + 网关端口 × 全部模型端口互斥检查。"""
from .result import Ok, Refused, is_unknown


def conflict_check(ctx, records):
    # type: (object, list) -> object
    """登记面互斥（**纯表读，不碰外部命令**）—— `BCR-R-27` 校验段第 4 步。

    返回 `None` 表示无冲突；有冲突返回 `Refused(7)` 并**逐条指名冲突对**。
    """
    seen, pairs = {}, []
    gw = ctx.gateway_port
    for r in records:
        p = r.machine.get("port")
        if p is None:
            continue
        if p == gw:
            pairs.append((r.id, "api", p))
        elif p in seen:
            pairs.append((seen[p], r.id, p))
        else:
            seen[p] = r.id
    if pairs:
        detail = "；".join(f"{a} 与 {b} 都用 {p}" for a, b, p in pairs)
        return Refused(7, "ports.conflict", detail=detail)
    return None


def alloc(ctx, records, listener_probe=None):
    # type: (object, list, object) -> object
    """分配一个端口。循环跳过：已注册 / 已监听 / 网关端口；**越界拒绝**。

    `listener_probe(port) -> Probe[int]`：`Unknown` 且**不是 NO_LISTENER** 时
    **不得采用该端口** —— lsof 跑完即失败会让「已被占用」被读成「空闲」。
    """
    lo, hi = ctx.port_range
    taken = {r.machine.get("port") for r in records} | {ctx.gateway_port}
    for p in range(lo, hi + 1):
        if p in taken:
            continue
        if listener_probe is not None:
            st = listener_probe(p)
            if not is_unknown(st):
                continue                      # Ok(pid) ⇒ 有人在监听
            if st.kind != "NO_LISTENER":
                return Refused(9, "ports.probe_undeterminable", port=p, kind=st.kind)
        return Ok(p)
    return Refused(7, "ports.exhausted", lo=lo, hi=hi)
