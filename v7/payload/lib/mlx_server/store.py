"""C 层 · 记录载体的**唯一写入者**。

锁内：读 → 校验 → 渲染 → 替换 → 重建内存视图。
**解析失败拒绝写，绝不空表重建**（INV-16）—— v6 的空表重建是数据丢失路径。
唯一性（id / 端口 / 来源三元组）在此强制。
"""
import json
import os

from . import atomic

MODELS = "models"          # target 是**枚举**而非路径（INV-15 在签名层成立）
SETTINGS = "settings"
_TARGETS = {MODELS: "models.jsonl", SETTINGS: "settings.json"}


class StoreParseError(Exception):
    """整文件不可解析 ⇒ 抛，**不返回空表**。调用方构造 `Refused(6)`。"""


class UniquenessError(Exception):
    """id / 端口 / 来源三元组冲突。"""


class BadLine:
    """坏行报告。**含行号** —— 「跳过坏行」必须可指名，否则静默丢数据。"""

    __slots__ = ("lineno", "reason_key", "raw")

    def __init__(self, lineno, reason_key, raw):
        self.lineno = lineno
        self.reason_key = reason_key
        self.raw = raw

    def __repr__(self):
        return f"BadLine({self.lineno}, {self.reason_key!r})"


# 两段各自的**已知字段**。开放集合（配置键可以落进 user 段），
# 所以这里不是「全部字段」，而是**已知归属**：出现在这里的字段，
# 从错误的那一段读就是缺陷。
#
# 权威是 `schema.tsv` 的 `scope` 列（`revision`/`size_mb` 标 machine，
# 配置键标 user）；`repo`/`type` 不是配置键，在这里补齐。
# 表读不到时（引导期、表损坏）回落到这份内置清单 —— 它只用于**报错**，
# 不参与任何判定，所以回落是安全的。
_KNOWN_SEG = {
    "repo": "user", "type": "user", "type_evidence": "machine",
    "revision": "machine", "size_mb": "machine", "layout": "machine",
    "port": "both",          # 端口两段都可能承载（`store.model_port` 逐个找）
}


class WrongSegment(KeyError):
    """从错误的段读一个已知字段。

    `dict.get` 打错段**静默返回 `None`**，而这个项目里 `None` 到处是合法值
    （未设置 / 未下载 / 不可判定）—— 于是它不会以异常的形式暴露，
    而是变成一个看起来正常的空值。已经发生过两次：
      · `weights` 产 `bytes` 而 `rebuild_machine` 找 `size_mb`（体积永远是「—」）
      · `model rm --weights` 从 user 段读 `revision`（永远取到 None ⇒
        退化成「删最新的那个 revision」，可能不是登记的那一个）
    """


class _Seg(dict):
    """记录的一段。**行为与 dict 相同，只在「读错段」时抛。**

    没有做成 `@dataclass(frozen=True)` 的理由：两段都是**开放集合** ——
    `config set <模型> --key=<任意配置键>` 会往 user 段写新键，
    `rebuild_machine` 会往 machine 段填新字段。固定字段集会把它们挡在外面。
    真正要抓的不是「有没有多余字段」，是**字段归属错了**。
    """

    __slots__ = ("_seg",)

    def __init__(self, seg, data):
        super().__init__(data)
        self._seg = seg

    def get(self, key, default=None):
        want = _KNOWN_SEG.get(key)
        if want is not None and want != "both" and want != self._seg:
            raise WrongSegment(
                f"{key!r} 在 {want} 段，不在 {self._seg} 段 —— "
                f"从这里读永远得到 None（而 None 在本项目里是合法值，不会报错）")
        return super().get(key, default)


class Record:
    """一条模型记录。user 段由人写，machine 段由工具填。"""

    __slots__ = ("id", "user", "machine")

    def __init__(self, rid, user, machine):
        self.id = rid
        self.user = _Seg("user", user)
        self.machine = _Seg("machine", machine)

    def to_json(self):
        return json.dumps({"id": self.id, "user": self.user, "machine": self.machine},
                          ensure_ascii=False, sort_keys=True)


class LoadResult:
    __slots__ = ("records", "bad_lines")

    def __init__(self, records, bad_lines):
        self.records = list(records)
        self.bad_lines = list(bad_lines)

    def by_id(self, rid):
        for r in self.records:
            if r.id == rid:
                return r
        return None


class ModelsTx:
    """target == MODELS 的事务。

    `patch` 里的 `type` 键做**域校验**（裁决 R2-2）：
    `type ∈ types.tsv 主键集合`，否则抛、不写盘 —— 否则 `backendarg`
    读到它时才炸，而那时记录已经写进 models.jsonl。
    """

    __slots__ = ("id", "port", "src", "patch", "mode", "user_keys", "unset_user")

    def __init__(self, rid, patch, mode="upsert", port=None, src=None,
                 user_keys=(), unset_user=()):
        self.id = rid
        self.patch = dict(patch)
        self.mode = mode
        self.port = port
        self.src = src
        # 调用方声明哪些键属 **user 段**。`config set <模型> --key=<k>` 写的是
        # 用户意图（`02:1437`：config 可写域 == scope==user），不是磁盘事实；
        # 此前它们一律落 machine 段 —— 于是 `config set b --key=port --value=<别人的端口>`
        # 绕过了 `store.commit` 的唯一性强制（那道检查只看 `tx.port`），
        # 两个模型并排显示同一个端口。
        self.user_keys = tuple(user_keys)
        # `config unset <模型> --key=X` 要**删掉** user 段的某个键 ——
        # 写 `None` 不等于删：`None` 会作为一个显式的「设成了空」参与三级链，
        # 而 unset 的语义是「让它回落到下一层」。
        self.unset_user = tuple(unset_user)


def _path(ctx, target):
    if target not in _TARGETS:
        raise ValueError(f"target 必须 ∈ {sorted(_TARGETS)}（枚举，不是路径），实得 {target!r}")
    return os.path.join(ctx.root_data, _TARGETS[target])


def load(ctx, target=MODELS):
    # type: (object, str) -> LoadResult
    """读 + 校验。**坏行跳过并报行号；整文件不可解析则抛。**"""
    p = _path(ctx, target)
    if not os.path.exists(p):
        return LoadResult([], [])
    try:
        with open(p, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as e:
        raise StoreParseError(f"{p} 读取失败：{e}") from e

    records, bad = [], []
    for lineno, rawline in enumerate(raw.splitlines(), 1):
        line = rawline.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad.append(BadLine(lineno, "store.bad_json", line[:80]))
            continue
        if not isinstance(obj, dict) or "id" not in obj:
            bad.append(BadLine(lineno, "store.missing_id", line[:80]))
            continue
        records.append(Record(obj["id"], obj.get("user", {}), obj.get("machine", {})))

    if records == [] and bad and len(bad) == len([x for x in raw.splitlines() if x.strip()]):
        raise StoreParseError(
            f"{p} 整文件不可解析（{len(bad)} 行全坏）—— **绝不空表重建**（INV-16）"
        )
    return LoadResult(records, bad)


class SettingsTx:
    """`settings.json` 的事务。**全局配置层**（三级链的第三级）。

    `02-architecture.md:1577` 把 `settings.json` 的唯一写者登记为 `store.commit`、
    唯一读者登记为 `schema.effective`。此前 `commit` 对这个 target 直接
    `raise NotImplementedError`，而 `cmd.config` 绕道去写了 `models.jsonl` ——
    配置键被当成模型 id 插进注册表，`mlx model ls` 里于是多出一个叫 `port`
    的「模型」，而 `config get` 读的还是出厂默认值。写侧与读侧从不相交。
    """

    __slots__ = ("patch", "unset")

    def __init__(self, patch=None, unset=()):
        self.patch = dict(patch or {})
        self.unset = tuple(unset)


def load_settings(ctx):
    # type: (object) -> dict
    """读全局设置。**单个 JSON 对象**，不是 jsonl（与 models 的载体不同）。

    缺失 ⇒ 空 dict（还没设过任何东西是合法状态）；
    不可解析 ⇒ 抛 `StoreParseError`，由边界层翻成 `Refused(6)` ——
    **不静默当空**，那会让用户的设置在一次手滑编辑后无声消失。
    """
    p = _path(ctx, SETTINGS)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as e:
        raise StoreParseError(f"{p} 读取失败：{e}") from e
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise StoreParseError(f"{p} 不是合法 JSON：{e}") from e
    if not isinstance(obj, dict):
        raise StoreParseError(f"{p} 顶层必须是对象，实得 {type(obj).__name__}")
    return obj


def _commit_settings(ctx, tx, stack):
    # type: (object, object, object) -> dict
    """锁内：读 → 合并 → 原子替换。返回合并后的全量设置。"""
    p = _path(ctx, SETTINGS)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # **传 `owns`**：陈旧锁按持有者存活判定后清理（`01-requirements.md:1296`）。
    # 不传的话 `atomic.lock` 里那整段四态判定是死代码 —— 写注册表期间进程被
    # 强杀（或系统强制睡眠）留下的锁目录永远没人清，此后**每一条**写命令都等满
    # 10 秒然后抛 LockTimeout，而清理机制就在那里、只是永不触发。
    # 用户唯一的出路是手工删一个他不知道存在的隐藏目录。
    with atomic.lock(p, stack, ctx, owns=_owns_pid):
        cur = load_settings(ctx)
        cur.update(tx.patch)
        for k in tx.unset:
            cur.pop(k, None)
        with atomic.replace(p) as fh:
            fh.write(json.dumps(cur, ensure_ascii=False, sort_keys=True, indent=2))
    return load_settings(ctx)


def _owns_pid(pid):
    # type: (int) -> object
    """锁持有者是否还活着。**三态** —— `Unknown` 不得塌成「已死」。

    塌了会清掉一个活进程正在持有的锁，两个进程同时改注册表。
    `proc.started_at_raw` 的 `DEAD`（ps 说没这个进程、stderr 为空）
    与 `PS_FAILED`（ps 自己出错）是两回事，`atomic.lock` 按 `is_dead()` 区分。
    """
    from . import proc
    return proc.started_at_raw(pid)


def commit(ctx, target, tx, stack=None, types=None):
    # type: (object, str, object, object, object) -> LoadResult
    """锁内：读 → 校验 → 渲染 → 原子替换 → 重建内存视图。"""
    if target == SETTINGS:
        return _commit_settings(ctx, tx, stack)
    if target != MODELS:
        raise NotImplementedError(f"target {target!r} 的 commit 尚未落地")
    p = _path(ctx, target)

    if "type" in tx.patch:
        allowed = set(types or ())
        if allowed and tx.patch["type"] not in allowed:
            raise ValueError(
                f"type={tx.patch['type']!r} ∉ types.tsv 主键集合 {sorted(allowed)} —— "
                "不写盘（否则 backendarg 读到它时才炸，而记录已经落地）"
            )

    # **传 `owns`**：陈旧锁按持有者存活判定后清理（`01-requirements.md:1296`）。
    # 不传的话 `atomic.lock` 里那整段四态判定是死代码 —— 写注册表期间进程被
    # 强杀（或系统强制睡眠）留下的锁目录永远没人清，此后**每一条**写命令都等满
    # 10 秒然后抛 LockTimeout，而清理机制就在那里、只是永不触发。
    # 用户唯一的出路是手工删一个他不知道存在的隐藏目录。
    with atomic.lock(p, stack, ctx, owns=_owns_pid):
        cur = load(ctx, target)
        recs = {r.id: r for r in cur.records}

        if tx.mode == "delete":
            recs.pop(tx.id, None)
        else:
            if tx.mode == "upsert" and tx.port is not None:
                for r in recs.values():
                    if r.id != tx.id and r.machine.get("port") == tx.port:
                        raise UniquenessError(
                            f"端口 {tx.port} 已被 {r.id!r} 占用（唯一性在 store 强制）")
            r = recs.get(tx.id) or Record(tx.id, {}, {})
            for k, v in tx.patch.items():
                # **`revision` 属 machine 段**：台账第 7 行的 effect_read_path
                # 逐字是 `store.load().record(<id>).machine.revision`，
                # 且 §1 写「`revision` 的唯一产者是 weights」—— 它是磁盘事实，
                # 不是用户填的。此前归入 user 段与台账相悖。
                seg = r.user if (k in ("repo", "type") or k in tx.user_keys) else r.machine
                seg[k] = v
            for k in getattr(tx, "unset_user", ()):
                r.user.pop(k, None)
            if tx.port is not None:
                r.machine["port"] = tx.port
            recs[tx.id] = r

        with atomic.replace(p) as fh:
            for rid in sorted(recs):
                fh.write(recs[rid].to_json() + "\n")
    return load(ctx, target)


def model_state(rec):
    # type: (object) -> str
    """一条记录的可读状态。**唯一实现点**（INV-20）——

    `status` 与 `model ls` 都要显示它；写两遍必然漂移，
    而漂移的症状是「两个命令对同一个模型给出不同状态」。
    判据全部来自磁盘事实，不猜。

    **返回值是数据，不是文案**（因此不进 `messages.tsv`）：它进 `--json` 的
    payload，机读消费者按它分派。要给人看的翻译在 `ui`。
    """
    get = rec.get if isinstance(rec, dict) else (lambda k, d=None: getattr(rec, k, d))
    machine = get("machine") or {}
    if not machine:
        return "已登记未下载"
    return "就绪" if machine.get("revision") else "已登记"


def model_port(rec):
    # type: (object) -> object
    """记录里登记的端口。user 段与 machine 段都可能承载它。"""
    get = rec.get if isinstance(rec, dict) else (lambda k, d=None: getattr(rec, k, d))
    for seg in ((get("user") or {}), (get("machine") or {})):
        if seg.get("port"):
            return seg["port"]
    return None


def rebuild_machine(ctx, disk_facts, stack=None):
    # type: (object, object, object) -> object
    """按磁盘事实重建 machine 段。**只填空不覆盖**（`04-iface-C.md` §1.5）。

    后置（逐字来自设计）：对每个字段，调用前 `machine.<f>` **非空时逐字节不变**；
    为空时取 `disk_facts.<f>`；输出**重建前后逐字段差异**。
    失败模式：`disk_facts` 不可判定 ⇒ **拒绝重建**（不用部分事实覆盖记录）。

    `revision` 因此仍只有一个产者（INV-03）—— 合并规则就是「只填空不覆盖」。
    """
    from .result import Done, Failed, Refused

    if disk_facts is None:
        # 「不用部分事实覆盖记录」——宁可不重建
        return Refused(9, "repair.facts_undeterminable")

    by_repo = {}
    for item in getattr(disk_facts, "installed", []) or []:
        if item.get("repo"):
            by_repo[item["repo"]] = item

    res = load(ctx, MODELS)
    diffs = []
    for rec in res.records:
        fact = by_repo.get(rec.user.get("repo"))
        if fact is None:
            continue
        filled = {}
        for field in ("revision", "size_mb"):
            before = (rec.machine or {}).get(field)
            after = fact.get(field)
            if before not in (None, "", 0) or after in (None, ""):
                continue                      # **非空则逐字节不变**
            filled[field] = after
            diffs.append({"id": rec.id, "field": field,
                          "before": before, "after": after})
        if filled:
            try:
                commit(ctx, MODELS, ModelsTx(rec.id, filled, mode="machine"), stack)
            except Exception as exc:          # noqa: BLE001
                # commit 失败 ⇒ 原文件逐字节不变
                return Failed(1, "repair.commit_failed", [], id=rec.id,
                              detail=str(exc))
    return Done({"kind": "repair", "changes": diffs,
                 "scanned": len(by_repo), "records": len(res.records)})
