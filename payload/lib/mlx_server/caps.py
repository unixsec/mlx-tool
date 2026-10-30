"""C 层 · **`share/caps.json` 的唯一写者与解释者**（`03b-ledgers.md:98`）。

拥有的资源：`share/caps.json`，`owns_kind == external`。

此前这些代码在 `cmd/caps.py`（D 层编排器）里，而契约明写「D 层全部成员
`owns_resource` 为空、`owns_kind == none` —— 编排层零自有资源」。
后果不只是分层不好看：`checks`(B) 与 `cmd.meta`(D) 要读能力视图，
只能反向 import 一个 D 层模块，那是五条层序反向边里的两条。

**写盘点也在这里**（`C-CAPS-T10` 是相等断言：「全项目对 `caps.json` 的
写入点集合 == {`caps`}」）。只搬读不搬写会造出一个「拥有资源却不写它」的
模块 —— 那当场违反下沉本身的立论。
"""
import json
import os

from . import atomic
from .result import Ok, Unknown, is_unknown

# **venv 三包的唯一定义点**。此前它在两个地方各写一份
# （`cmd/caps.py:PACKAGES` 与 `runtime.py:VENV_PACKAGES`），靠 dict key 的
# 字面相等耦合、零守卫 —— `runtime.fingerprint` 按一份生成那串 JSON，
# `caps` 按另一份去筛它的 key。设计对这类形态的原话是
# 「判定与它的证据同居」（`02-architecture.md:1806`），两份常量正是反着做。
PACKAGES = ("mlx", "mlx_lm", "mlx_vlm")

# **探测面 == 安装面的后端部分。** `install.sh` 装 `huggingface_hub mlx mlx-lm mlx-vlm`；
# `huggingface_hub` 不是后端、不进 fingerprint，所以不在 `PACKAGES` 里。
# mlx_vlm 此前是「可选」：它只服务 `--type=vision`，而那条路径根本起不来
# （1.0.0 的已知缺陷）。现在多模态模型经 `up` 现场判定路由到它（spec D3），
# 缺了它 `up` 就拒绝（P1）—— 一个缺了就拒绝启动的包不是可选包。
# 「必需」与「可选」仍分两个常量：`mlx version` 只把必需包的缺失报成缺陷。
REQUIRED = ("mlx", "mlx_lm", "mlx_vlm")
OPTIONAL = {}


def path(ctx):
    # type: (object) -> str
    """`caps.json` 的落点。按路径台账（`02-architecture.md:374/389`）：
    **代码根的 `share/`**，处置「生成」，在备份面之外。"""
    from .ctx import share_of
    return os.path.join(share_of(ctx), "caps.json")


def get(ctx):
    # type: (object) -> dict
    """读缓存的能力视图。缺失返回空 —— **不即时探测**（那是 refresh 的职责）。"""
    p = path(ctx)
    if not os.path.exists(p):
        # 落点从数据根搬到了 `share/`。旧位置还有的话就读它 ——
        # 否则升级后用户的第一条 `up` 会因「尚未采集」被拒，而他明明采集过。
        # 下一次 refresh 会写到新位置。
        legacy = os.path.join(ctx.root_data, "caps.json")
        if os.path.exists(legacy):
            p = legacy
        else:
            return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except ValueError:
        return {}


def write(ctx, view):
    # type: (object, dict) -> None
    """**唯一写点**（`C-CAPS-T10`：全项目对 `caps.json` 的写入点集合 == {caps}）。"""
    with atomic.replace(path(ctx)) as fh:
        fh.write(json.dumps(view, ensure_ascii=False, sort_keys=True))


def build_view(fingerprint_probe, now):
    # type: (object, object) -> dict
    """把一次指纹探测结果整理成能力视图。**纯函数**（探针值由调用方注入）。

    返回 `dict`；探测确定失败（非 `TOOL_UNAVAILABLE`）时返回 `None`，
    由调用方构造 `Refused(9)` —— 「探不到」与「探到了没装」是两回事。
    """
    import time
    if is_unknown(fingerprint_probe):
        if fingerprint_probe.kind != "TOOL_UNAVAILABLE":
            return None
        # **「venv 里没装那三个包」是一个可采集的事实**，不是探测失败。
        # 记下这个基线，否则 drift 永远判不出、模型服务永远起不来。
        # 装上包之后指纹会变，drift 照样能抓到 —— 守卫没被削弱。
        view = {"fingerprint": "", "packages": [], "note": "venv_absent"}
    else:
        # `packages` 是**实装清单**，不是常量清单。此前这里写 `list(PACKAGES)`，
        # 于是 `mlx version` 一边说「依赖三包 mlx、mlx_lm、mlx_vlm」，
        # 一边在 fingerprint 里显示 `mlx_vlm: null` —— 同一屏自相矛盾。
        # 指纹是 **JSON 字符串**（`runtime.fingerprint` 末行；drift 靠字符串
        # 相等比较，不能改成 dict），所以这里要解析后再筛。
        try:
            fp = json.loads(fingerprint_probe.value)
        except (TypeError, ValueError):
            fp = {}
        view = {"fingerprint": fingerprint_probe.value,
                "packages": [k for k in PACKAGES
                             if isinstance(fp, dict) and fp.get(k)]}
    # 采集时间是 `version` 的契约字段（附录 A：「含 caps.json 采集时间」）。
    view["collected_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now()))
    return view


def diff(prev, view):
    # type: (dict, dict) -> list
    """两次采集之间的差异。**纯函数。**

    附录 A 2831：`refresh` 的主产物是采集结果差异，空结果是「无变化 → 合法空数组」。
    此前返回的是全量视图 —— 连跑两次输出逐字节相同，用户看不出这次采集改变了什么。
    """
    out = []
    for k in sorted(set(prev) | set(view)):
        if k == "collected_at":        # 每次必变，不是「采集结果」的一部分
            continue
        before, after = prev.get(k), view.get(k)
        if before != after:
            out.append({"item": k, "before": before, "after": after})
    return out


def drift(ctx, probe_fingerprint=None):
    # type: (object, object) -> object
    """venv 三包指纹是否与缓存不符。**三态** —— `Unknown` 不得塌成 `Ok(False)`。

    塌了会让「venv 被换过」这件事静默通过，而 argv 是按旧能力拼的。
    """
    cached = get(ctx)
    if not cached:
        # **「从未采集」不是「探测失败」**：前者是可预期的初始状态且有明确下一步
        # （跑 caps refresh），后者是异常。都塌成 Unknown 会让首次使用被 fail-closed
        # 永久锁死 —— 用户拿到「不可判定」却不知道该做什么。
        return Unknown("NOT_COLLECTED", "尚未采集能力信息")
    if probe_fingerprint is None:
        return Unknown("TOOL_UNAVAILABLE", "指纹探针未注入")
    cur = probe_fingerprint()
    if is_unknown(cur):
        if cur.kind == "TOOL_UNAVAILABLE" and cached.get("note") == "venv_absent":
            return Ok(False)          # 采集时就没有 venv，现在也没有 ⇒ 没漂移
        return Unknown(cur.kind, cur.reason)
    return Ok(cur.value != cached.get("fingerprint"))
