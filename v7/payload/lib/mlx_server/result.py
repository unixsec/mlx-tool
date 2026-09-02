"""A 层 · 三态载体。零 I/O，可在任何环境 import。

`Probe[T]` 是探针三态：Ok(值) / Unknown(理由)。
**没有第三种「用默认值假装成功」的形态** —— INV-09 禁止用 None 表达失败。
"""


# `Unknown` 的 kind 闭集。**不是自由文本** —— T-26 §二② 逐字要求
# DEAD 与 UNKNOWN 是两个分开的态：DEAD 可清理，UNKNOWN 一律 fail-closed 不得动手。
# 靠 reason 字符串前缀区分会让「ps 被改 PATH / 权限不足 / 参数错」都被读成
# 「进程不存在」⇒ 触发陈旧锁清理 ⇒ 而那个 28GB 后端其实活着。
UNKNOWN_KINDS = frozenset({"DEAD", "PS_FAILED", "TOOL_UNAVAILABLE", "TIMEOUT",
                           "NO_LISTENER", "PARSE_FAILED", "NOT_COLLECTED"})


class Probe:
    """探针结果三态。3.9 下限：不用 dataclass(slots=True)、不用 X | Y 注解。"""

    __slots__ = ("_ok", "_value", "_reason", "_kind")

    def __init__(self, ok, value=None, reason=None, kind=None):
        # type: (bool, Optional[object], Optional[str], Optional[str]) -> None
        if not ok and kind not in UNKNOWN_KINDS:
            raise ValueError(
                f"Unknown 的 kind 必须 ∈ {sorted(UNKNOWN_KINDS)}，实得 {kind!r} —— "
                "不得用自由文本区分态（T-26 §二②：DEAD 与 UNKNOWN 的处置相反）"
            )
        self._ok = ok
        self._value = value
        self._reason = reason
        self._kind = kind

    @property
    def value(self):
        # type: () -> object
        if not self._ok:
            raise ValueError(f"Unknown({self._reason}) 没有值 —— 消费点必须先判 is_unknown")
        return self._value

    @property
    def reason(self):
        # type: () -> Optional[str]
        return self._reason

    @property
    def kind(self):
        # type: () -> Optional[str]
        """`Unknown` 的态。**判定必须读它，不得解析 reason 文本。**"""
        return self._kind

    def is_dead(self):
        # type: () -> bool
        """「确知已死」—— 唯一允许清理的态。其余 Unknown 一律 fail-closed。"""
        return (not self._ok) and self._kind == "DEAD"

    def is_unknown(self):
        # type: () -> bool
        return not self._ok

    def __repr__(self):
        # type: () -> str
        return f"Ok({self._value!r})" if self._ok else f"Unknown({self._kind}, {self._reason!r})"

    def __eq__(self, other):
        # type: (object) -> bool
        if not isinstance(other, Probe):
            return NotImplemented
        return ((self._ok, self._value, self._kind)
                == (other._ok, other._value, other._kind))


def Ok(value):
    # type: (object) -> Probe
    return Probe(True, value=value)


def Unknown(kind, reason=None):
    # type: (str, Optional[str]) -> Probe
    """`kind` 必须 ∈ `UNKNOWN_KINDS`；`reason` 是给人看的补充，**判定不得读它**。"""
    return Probe(False, kind=kind, reason=reason)


# ── Outcome 三态（格 4：载荷类型）──────────────────────────────────
#
# 只有 Done 能产生 rc 0。Refused 与 Failed 的区别是 **BCR-R-01 的字面**：
#   Refused 的唯一前提是「**零资源已创建**」—— 不是「必须在校验段」。
#   已在 stage_split 之后而仍构造 Refused 是合法的（DE:189 明写），
#   supervisor.up 必然在 stage_split 之后，也返回 Refused(3)/Refused(9)。
#   Failed 则**必须**带 committed（已落地的资源清单），供调用方指名收敛动作。

_REFUSED_CODES = frozenset({2, 3, 5, 6, 7, 8, 9})
_FAILED_CODES = frozenset({1, 3, 4, 10})


class Done:
    """成功。**唯一能产生 rc 0 的构造子。**"""

    __slots__ = ("payload",)

    def __init__(self, payload=None):
        # type: (object) -> None
        self.payload = payload

    @property
    def rc(self):
        # type: () -> int
        return 0

    def __repr__(self):
        # type: () -> str
        return f"Done({self.payload!r})"


class Refused:
    """拒绝。**前提：零资源已创建**（BCR-R-01）。"""

    __slots__ = ("code", "msg_key", "args")

    def __init__(self, code, msg_key, **args):
        # type: (int, str, object) -> None
        if code not in _REFUSED_CODES:
            raise ValueError(
                f"Refused 的 code 必须 ∈ {sorted(_REFUSED_CODES)}，实得 {code}"
            )
        self.code = code
        self.msg_key = msg_key
        self.args = args

    @property
    def rc(self):
        # type: () -> int
        return self.code

    def __repr__(self):
        # type: () -> str
        return f"Refused({self.code}, {self.msg_key!r})"


# 部署硬门的五个码。**T0 独占 + `cmd.self` 透传**（BCR-2026-08-30 · §4.3）
_PASSTHROUGH_CODES = frozenset({70, 71, 72, 73, 74})


# 聚合结果码。`check` 与 `config validate` 共用同一对（`02` §4.3）
_AGGREGATE_CODES = frozenset({64, 65})


class Aggregate:
    """多项检查的**聚合结果**：64 含告警 / 65 含失败。

    与 `Failed` 的关键区别：**它携带主产物**。`check` 的契约是「九项各一行」，
    失败时用户最需要看到的正是那九行 —— 用 `Failed` 表达会让主产物丢掉，
    只剩一行文案键，用户看不出是哪几项挂了（实测过这个后果）。
    """

    __slots__ = ("code", "payload", "msg_key")

    def __init__(self, code, payload, msg_key=None):
        # type: (int, object, object) -> None
        if code not in _AGGREGATE_CODES:
            raise ValueError(
                f"Aggregate 的 code 必须 ∈ {sorted(_AGGREGATE_CODES)}"
                f"（64 含告警 / 65 含失败），实得 {code}")
        self.code = code
        self.payload = payload
        self.msg_key = msg_key

    @property
    def rc(self):
        # type: () -> int
        return self.code


class Passthrough:
    """T0 部署硬门码的**受限透传**。唯一构造点 `cmd.self`。

    为什么需要一个新构造子：`install.sh` 的分发清单校验失败会 `exit 71`，
    而 `Done/Refused/Failed` 的码域并集是 `{0..10}` —— 透传不出来。
    两条错误的出路：
      · 重新解释成 1 或 3 ⇒ 五个硬门码塌成一个，用户丢掉「哪一项没过」，
        而那正是 §4.3 引入五个独立码的唯一理由（「一道门在两个方向都不可靠」）；
      · 放宽 `Failed` 的允许码 ⇒ 每个 `Failed` 都能产 70–74，值域不再是闭的。

    因此本构造子的**值域与构造点都是闭的**，且 `code` 必须来自子进程的真实
    退出码，不得凭空构造。
    """

    __slots__ = ("code", "msg_key", "args")

    def __init__(self, code, msg_key, **args):
        # type: (int, str, object) -> None
        if code not in _PASSTHROUGH_CODES:
            raise ValueError(
                f"Passthrough 的 code 必须 ∈ {sorted(_PASSTHROUGH_CODES)}"
                f"（T0 部署硬门），实得 {code}")
        self.code = code
        self.msg_key = msg_key
        self.args = args

    @property
    def rc(self):
        # type: () -> int
        return self.code


class Failed:
    """失败，**且已有资源落地**。`committed` 是收敛线索，不得为 None。"""

    __slots__ = ("code", "msg_key", "committed", "args")

    def __init__(self, code, msg_key, committed, **args):
        # type: (int, str, list, object) -> None
        if code not in _FAILED_CODES:
            raise ValueError(
                f"Failed 的 code 必须 ∈ {sorted(_FAILED_CODES)}，实得 {code}"
            )
        if committed is None:
            raise ValueError(
                "Failed.committed 不得为 None —— 它是「已落地了什么」的唯一线索，"
                "缺了它调用方无从指名收敛动作（空列表表示确实零落地，与 None 不同）"
            )
        self.code = code
        self.msg_key = msg_key
        self.committed = list(committed)
        self.args = args

    @property
    def rc(self):
        # type: () -> int
        return self.code

    def __repr__(self):
        # type: () -> str
        return f"Failed({self.code}, {self.msg_key!r}, committed={self.committed!r})"


def probe_or(default, r):
    # type: (object, Probe) -> object
    """展示层的塌陷器：`Ok(v) → v`；`Unknown(_) → default`。

    **前置（硬）**：调用点不得出现在任何判定路径上（`@verdict` 传递闭包内）——
    它把三态塌成两态，在判定路径上用它就是 fail-open。守卫 `L0·result-03`。
    """
    return default if r.is_unknown() else r.value


def is_unknown(p):
    # type: (Probe) -> bool
    """判定路径上**唯一**允许的 Unknown 写法（裁决 D-10）。"""
    return p.is_unknown()
