"""B 层 · 运行时上下文。**环境读取权的所有者**（`owns_kind == authority`）。

四处断口的落地（04-interfaces.md §2.2）：
  ① `constants` 没有产者   ⇒ 由 ctx 自己读 share/constants.env（它已是环境读取权所有者）
  ② 三根在生产环境无来源   ⇒ 取值序写死：**environ 优先、constants 兜底**；
                              两个来源都缺才抛且指名键。垫片是三行、不导出任何环境变量，
                              若照原契约「缺 MLX_ROOT_DATA 即抛」，装完第一条命令就崩。
  ③ BCR-A-03 循环依赖      ⇒ `proc.started_at_raw` 取标量入参，build() 在构造前调它
  ④ 两个构造符号名          ⇒ build() 对外（边 #9），load() 是它的纯函数内核
"""
import os

from .proc import started_at_raw

_ROOT_KEYS = ("MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS")


class MissingConstant(Exception):
    """两个来源都缺某个键。**必须指名是哪个键** —— 静默默认值要被抓住。"""


# 外部命令的出厂路径。**绝对路径**是有意的：`env -i` 之后 PATH 里
# 可能没有它们，而这些命令是存活判定的证据来源 —— 让它们随 PATH 漂移，
# 等于让「进程还在不在」的答案取决于调用者的环境。
DEFAULT_BINS = {
    "ps": "/bin/ps",
    "lsof": "/usr/sbin/lsof",
    "pgrep": "/usr/bin/pgrep",
    "vm_stat": "/usr/bin/vm_stat",
}


def share_of(ctx):
    # type: (object) -> str
    """`share/` 在哪。**全项目取它的唯一方式** —— 别处不要再拼这个字面量。

    三处独立推导（垫片 / `entry._share_root` / `root_code + "share"`）今天一致
    只因为安装器与测试都显式设了 `MLX_SHARE_ROOT`；用户设一个别的值，
    表从 A 读、`caps.json` 写到 B，而没有任何东西会红。
    """
    return getattr(ctx, "share_root", None) or os.path.join(ctx.root_code, "share")


class Ctx:
    """冻结上下文。**不持有可变的栈**（边 #10：冻结数据类持可变栈会产生别名问题）。"""

    # 注：**没有 `_frozen` 槽位** —— 上一版有，但全代码零读取，是纯装饰。
    # 「冻结」由 __setattr__/__delattr__ 承担；`object.__setattr__` 仍可绕过，
    # 那是 Python 语言层不可堵的，靠 L0 守卫「产品里 object.__setattr__ 的
    # 出现点集合 == {Ctx.__init__}」兜住（见 test_ctx.py）。
    __slots__ = ("root_code", "root_data", "root_weights", "venv", "gateway_port",
                 "port_range", "self_started_at", "bins", "share_root")

    def __init__(self, root_code, root_data, root_weights, venv,
                 gateway_port, port_range, self_started_at, bins=None, share_root=""):
        # type: (str, str, str, int, Tuple[int, int], object) -> None
        object.__setattr__(self, "root_code", root_code)
        object.__setattr__(self, "root_data", root_data)
        object.__setattr__(self, "root_weights", root_weights)
        object.__setattr__(self, "venv", venv)
        object.__setattr__(self, "gateway_port", gateway_port)
        object.__setattr__(self, "port_range", port_range)
        # **外部命令路径的注入面**（`02-architecture.md` §8.2 的可注入项之一）。
        # 此前它们是十处函数默认参数里的字面量，产品从不读 `MLX_BIN_*` ——
        # 于是 L2 里那个注了 `MLX_BIN_PS` 的 ps 桩**就是摆设**，
        # 那条用例实际跑在真 `/bin/ps` 上，`ps`/`lsof` 的三态映射
        # 在 L2 层没有任何被注入过的坏样本。
        object.__setattr__(self, "bins", dict(bins or DEFAULT_BINS))
        # **`share/` 的唯一来源**。此前有三处独立推导（垫片、`entry._share_root`、
        # `runtime._tables`/`tables.open_tables` 各自 `root_code + "share"`），
        # 三者今天一致只因为安装器与测试都显式设了 `MLX_SHARE_ROOT` ——
        # 用户自己设一个别的值，表从 A 读、caps.json 写到 B。
        object.__setattr__(self, "share_root",
                           share_root or os.path.join(root_code, "share"))
        object.__setattr__(self, "self_started_at", self_started_at)

    def __setattr__(self, name, value):
        # type: (str, object) -> None
        raise AttributeError(f"Ctx 是冻结的，不得改 {name}")

    def __delattr__(self, name):
        # type: (str) -> None
        raise AttributeError(f"Ctx 是冻结的，不得删 {name}")


def _parse_constants(text):
    # type: (str) -> Dict[str, str]
    """把 KEY=VALUE 文本解析成映射。纯函数。"""
    out = {}                                    # type: Dict[str, str]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def load(constants, environ, self_started_at):
    # type: (Mapping[str, str], Mapping[str, str], object) -> Ctx
    """**纯函数内核**（断口 ④）：不做任何 I/O，取值序与校验都在这里。

    断口 ②：environ 优先、constants 兜底；两个来源都缺 → 抛且指名键。
    """
    def pick(key):
        # type: (str) -> str
        v = environ.get(key) or constants.get(key)
        if not v:
            raise MissingConstant(
                f"{key} 在 environ 与 constants.env 中都缺失 —— "
                "不回落默认路径（静默默认值会让三根指向错误位置而无人察觉）"
            )
        return v

    code, data, weights = (pick(k) for k in _ROOT_KEYS)
    port = int(pick("MLX_GATEWAY_PORT"))
    lo, _, hi = pick("MLX_PORT_RANGE").partition("-")
    # `MLX_VENV` **不走 pick**：venv 缺席是合法状态（垫片会回落系统 python3，
    # `00-inheritance` §0.4.2），用 pick 会让所有既有安装的第一条命令就抛。
    # 缺失时从代码根推导 —— 那是安装器建它的位置，不是「猜一个默认路径」。
    venv = (environ.get("MLX_VENV") or constants.get("MLX_VENV")
            or os.path.join(code, ".venv"))
    # `MLX_BIN_<NAME>` 覆盖单个外部命令的路径（测试注桩用；生产不设即出厂值）
    bins = dict(DEFAULT_BINS)
    for name in DEFAULT_BINS:
        override = environ.get("MLX_BIN_" + name.upper())
        if override:
            bins[name] = override
    return Ctx(code, data, weights, venv, port, (int(lo), int(hi)),
               self_started_at, bins, environ.get("MLX_SHARE_ROOT", ""))


def build(share_root, environ=None, pid=None):
    # type: (str, Optional[Mapping[str, str]], Optional[int]) -> Ctx
    """**唯一的构造入口**（断口 ④，边 #9）。做 I/O，然后委托 load()。

    断口 ①：constants 由本函数自己读，不要求调用方传入 —— 全书没有别的模块被指派读它。
    断口 ③：先用标量入参调 started_at_raw，再构造 Ctx，避免「构造 Ctx 需要 Ctx」。
    """
    env = os.environ if environ is None else environ
    path = os.path.join(share_root, "constants.env")
    try:
        with open(path) as fh:
            constants = _parse_constants(fh.read())
    except OSError:
        constants = {}                          # 文件缺失不即抛：environ 可能给全了
    st = started_at_raw(os.getpid() if pid is None else pid)
    return load(constants, env, st)
