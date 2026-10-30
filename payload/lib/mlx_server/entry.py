"""E 层 · 进程入口。**rc 的唯一映射点**（`02` §1.7）。

`rc := 5 if (中断标志已置 ∧ 本次调用尚未产出 Outcome) else rc(Outcome)`
"""
import os
import sys

from . import backendarg, cli, ctx, runtime
from .cmd import api as cmd_api
from .cmd import bench as cmd_bench
from .cmd import bootstrap as cmd_bootstrap
from .cmd import caps as cmd_caps
from .cmd import chat as cmd_chat
from .cmd import check as cmd_check
from .cmd import config as cmd_config
from .cmd import meta as cmd_meta
from .cmd import model_acquire, model_query, model_reclaim
from .cmd import self_deploy as cmd_self
from .cmd import svc as cmd_svc
from .result import Aggregate, Done
from .tables import TableError, TablesView

# `verbs.tsv#impl` 的字符串 → 模块对象。**这是本文件唯一的动词面知识**：
# 哪些动词存在、走哪条路径、要几个位置参数，全部在表里。
# 加一个动词改表，不改这里；加一个**模块**才改这里。
MODULES = {
    "cmd.svc": cmd_svc,
    "cmd.model.acquire": model_acquire,
    "cmd.model.reclaim": model_reclaim,
    "cmd.model.query": model_query,
    "cmd.config": cmd_config,
    "cmd.caps": cmd_caps,
    "cmd.bootstrap": cmd_bootstrap,
    "cmd.check": cmd_check,
    "cmd.api": cmd_api,
    "cmd.bench": cmd_bench,
    "cmd.chat": cmd_chat,
    "cmd.meta": cmd_meta,
    "cmd.self": cmd_self,
    "cli": None,             # help —— 在 main 里短路，不进 D 层编排
}


def _load_renderers():
    """import 各 `cmd/render_*.py`，它们在模块级调 `ui.register()` 自我登记。

    **由装配层做这件事**：`ui` 自己去找 `cmd` 是 B 层 import D 层（反向边）；
    而在 `ui` 里手写一张 `kind → 函数` 的表，等于把耦合换个地方放 ——
    加一个动词仍要改 `ui`。装配点认识所有层，这是它的职责。
    """
    import importlib
    import pkgutil

    from . import cmd as _cmd
    for m in pkgutil.iter_modules(_cmd.__path__):
        if m.name.startswith("render_"):
            importlib.import_module(f".cmd.{m.name}", __package__)


def route_for(inv, verb_rows):
    # type: (object, list) -> object
    """`Invocation` → D 层模块。**查的是台账，不是本文件里的第二份动词表。**"""
    group = (inv.args or {}).get("_group")
    for r in verb_rows:
        if group:
            if r["parent"] == group and r["verb"] == inv.verb:
                return MODULES.get(r["impl"])
        elif not r["parent"] and r["verb"] == inv.verb:
            return MODULES.get(r["impl"])
    return None


def _dispatch(inv, c, stack, verbs):
    # type: (object, object, object, list) -> object
    """把 Invocation 送到对应的 D 层编排。

    deps 从 **`runtime.build_deps`** 取 —— 那是生产实现的唯一装配点。
    测试注自己的桩，两者互不影响。
    """
    mod = route_for(inv, verbs)
    if mod is None:
        from .result import Refused
        return Refused(2, "cmd.unknown_verb", verb=inv.verb)

    _load_renderers()          # 渲染器自我登记（装配期，一次）
    deps = runtime.build_deps(c)
    deps["backendarg_spec"] = backendarg.spec
    return mod.run(c, stack, inv, deps)


# 边界层认得的异常 → 退出码。**不是「兜住一切」**：未登记的异常仍该
# 冒出 traceback，那是缺陷信号，吞掉它比裸抛更坏。
_BOUNDARY_MAP = (
    ("StoreParseError", 6, "store.unparseable"),
    ("UniquenessError", 7, "store.uniqueness"),
    ("LockTimeout", 7, "lock.timeout"),
    ("TableError", 3, "tables.unreadable"),
    # 运行记录存在但读不了 —— **不是「没在跑」**，两者处置相反
    ("RunRecordUnreadable", 6, "run.unreadable"),
)


def _boundary_exc_types():
    from . import atomic, store, supervisor, tables
    return {"StoreParseError": store.StoreParseError,
            "UniquenessError": store.UniquenessError,
            "LockTimeout": atomic.LockTimeout,
            "TableError": tables.TableError,
            "RunRecordUnreadable": supervisor.RunRecordUnreadable}


def _boundary_outcome(exc):
    # type: (Exception) -> object
    """把边界异常翻成 `Refused`，并把异常自己的话带给用户。"""
    from .result import Refused
    name = type(exc).__name__
    for n, code, key in _BOUNDARY_MAP:
        if n == name:
            return Refused(code, key, detail=str(exc)[:400])
    raise exc                       # 未登记 ⇒ 原样抛，别把缺陷藏起来


def _share_root(environ=None):
    # type: (Optional[Mapping[str, str]]) -> str
    """`share/` 在哪。**这是代码自身的位置，不是用户配置** ——

    与「三根不得静默回落默认值」不冲突：那条禁止的是替用户猜*配置*，
    而 share/ 与本文件的相对位置是安装布局里的确定事实。

    旧写法 `environ.get("MLX_SHARE_ROOT", "")` 的祸害：取不到时传空串，
    `constants.env` 就成了相对路径，open 失败被 ctx 当成「文件缺失」吞掉，
    三根随之全部落空 —— 而真实用户装完只往 PATH 加一行，从不设这个变量。
    """
    env = os.environ if environ is None else environ
    v = env.get("MLX_SHARE_ROOT")
    if v:
        return v
    here = os.path.dirname(os.path.abspath(__file__))       # <code_root>/lib/mlx_server
    return os.path.join(os.path.dirname(os.path.dirname(here)), "share")


def _render(payload, inv, tables=None):
    # type: (object, object, object) -> str
    """主产物的渲染出口。`--json` 给机器，否则给人。"""
    if (inv.args or {}).get("json"):
        import json as _json
        return _json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    from . import ui
    return ui.human(payload, tables)


def main(argv, out=None, err=None):
    # type: (list, object, object) -> int
    """**每条可达出口都显式 return int** —— 不得 fall-through（审视 P0-2）。"""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err

    share = _share_root()
    tv = TablesView(share)
    try:
        verbs = tv.verbs()
        cli_rows = tv.cli()
    except TableError as e:
        # 动词台账读不到 = 安装坏了。**不回落到硬编码的动词表** ——
        # 那会让「表驱动」变成「表加代码两份事实」，而这正是 PROD-08 要消灭的。
        err.write(f"动词台账不可读：{e}\n  安装可能不完整，试 `bash deployer/install.sh` 重装\n")
        return 3

    # help 短路在解析第一阶段（`02` §4.1.3 第 2 条）：走 stdout，stderr 保持为空
    if cli.is_help_request(argv):
        text, rc = cli.render_help(argv, verbs, cli_rows, tv)
        out.write(text)
        return rc

    try:
        inv = cli.parse(argv, verbs, cli_rows)
    except cli.ParseError as e:
        err.write(f"{e}\n")
        err.write(cli.usage(verbs, tv))
        return e.code

    try:
        c = ctx.build(_share_root())
    except ctx.MissingConstant as e:
        err.write(f"{e}\n")
        return 6

    from .cleanup import CleanupStack
    stack = CleanupStack()
    try:
        outcome = _dispatch(inv, c, stack, verbs)
    except KeyboardInterrupt:
        # 手册与 `mlx help` 的退出码段都写着「5 用户取消、确认串失配或**中断（Ctrl-C）**」，
        # 而此前 Ctrl-C 走的是解释器默认路径：一段 traceback + 被信号终止（shell 看到 130）。
        # 确认提示上按 Ctrl-C 尤其难看 —— 那正是用户最可能按它的时刻。
        from .result import Refused
        err.write("\n")
        outcome = Refused(5, "cmd.interrupted")
    except tuple(_boundary_exc_types().values()) as exc:
        # **边界层的异常→Outcome 映射。** `store.load` 的 docstring 逐字写着
        # 「整文件不可解析 ⇒ 抛，不返回空表。**调用方构造 `Refused(6)`**」——
        # 契约写了，而每一个调用方都是裸调。后果：注册表损坏后
        # `model ls` / `register` / `up` / `bench` / `chat` 全是裸 traceback + rc 1，
        # 连 `check` 指给用户的那条修复命令自己也崩，整个工具没有出路。
        # 映射放在这里而不是每个 D 模块里：出口只有一个，逐个加必然漏。
        outcome = _boundary_outcome(exc)
    finally:
        stack.unwind()

    import json

    if isinstance(outcome, Done):
        pay = outcome.payload
        if inv.dry_run and isinstance(pay, dict) and "argv" in pay:
            # 启动类动词的 `--dry-run`：打印 spec().argv 与每项配置的来源层（BCR-R-27）
            out.write(json.dumps(list(pay["argv"])) + "\n")
            for k, layer in sorted(pay.get("sources", {}).items()):
                err.write(f"  {k}: {layer}\n")
            if pay.get("env"):
                err.write("  env   " + "  ".join(f"{k}={v}" for k, v in pay["env"].items())
                          + "\n")
            rt = pay.get("route")
            if rt:
                err.write(f"  判定  {rt['rule']} · {rt['backend']} · 依据 {rt['vision_src']}"
                          f" · thinking={'开' if rt['thinking'] else '关'}"
                          f" · effort={','.join(rt['effort_domain']) or '（不传）'}"
                          + (f"（模板判不出：{rt['template_note']}，按不传处理）"
                             if rt.get("template_note") else "") + "\n")
                # R2（挂了独立完整 drafter 的 mlx_lm）实际逐请求串行处理——
                # `max_concurrency` 在这条路由下不生效，显示「并发上限 N」
                # 会夸大真实并发能力，统一改显示「串行」（spec §2.4 R2 行，C1）。
                conc = ("串行" if rt.get("rule") == "R2"
                        else f"并发上限 {rt.get('max_concurrency')}")
                err.write(f"  就绪  {rt['ready']} · 最多等 {pay.get('ready_timeout_sec')} 秒"
                          f" · {conc}\n")
            if pay.get("interpreter_predicted"):
                err.write(f"  解释器 {pay['argv'][0]}（按文件预测为 {pay['interpreter_predicted']}，"
                          "启动时实测：venv 解释器 import 不了网关就回落系统解释器）\n")
            if pay.get("dropped"):
                err.write("  已退役的键：" + "、".join(pay["dropped"])
                          + "（`mlx model repair` 清除）\n")
        elif inv.dry_run:
            # **其余动词的 dry-run 与正常输出同一出口** —— `--dry-run` 不是
            # 输出格式上的特例，它只是「不真做」。此前这里自行 json.dumps，
            # 于是 `mlx install --dry-run` / `mlx bench <id> --dry-run`
            # 默认吐 JSON，人类路径根本够不着（`--json` 与不加逐字节相同）。
            out.write(_render(pay, inv, tv) + "\n")
        else:
            # 主产物走 stdout（INV-17）。**默认给人看，`--json` 才给机器看** ——
            # 附录 A 的「机读」列写的是 `--json`，默认吐压缩 JSON 是实现反了。
            body = _render(pay, inv, tv)
            if body:
                out.write(body + "\n")
            elif isinstance(pay, dict) and pay.get("rounds") is not None:
                # `chat` 多轮：正文已经边聊边写进 stdout 了，这里只把汇总
                # 补到 stderr —— 台账要求 stdout **逐字节只含回复正文**。
                err.write(f"  {pay.get('rounds')} 轮 · 会话 {pay.get('session')}"
                          f"（`--session={pay.get('session')}` 接着聊）\n")
        return outcome.rc

    if isinstance(outcome, Aggregate):
        # 聚合结果（64 含告警 / 65 含失败）**携带主产物** ——
        # `check` 的契约是「九项各一行」，失败时用户最需要的正是那九行。
        out.write(_render(outcome.payload, inv, tv) + "\n")
        if outcome.msg_key:
            # **走同一个查表出口**，不写死映射 —— 写死就是文案的第二处定义，
            # 改了 messages.tsv 而这里不改，两处会各说各的。
            try:
                err.write(tv.text(tv.msg(outcome.msg_key)) + "\n")
            except Exception:                      # noqa: BLE001
                err.write(outcome.msg_key + "\n")
        return outcome.rc

    # 多目标的逐个结论是**主产物**（附录 A：「每个目标一行结论」），
    # 部分失败时它比那句总判有用得多 —— check 的九行也是同一道理。
    tgt_results = getattr(outcome, "args", {}).get("results")
    if tgt_results:
        out.write(_render({"kind": "svc", "targets": sorted(tgt_results),
                           "results": tgt_results}, inv, tv) + "\n")

    # Refused / Failed：过程说明走 stderr。**必须渲染成文案，不打印裸 key**
    # （L1·ui-10）—— 出错正是用户最需要看懂的时刻，给他一个 `model.not_found`
    # 等于把机器标识符甩在脸上。表里没有该键时才回落到键名，并附上参数。
    key = getattr(outcome, "msg_key", "unknown")
    args = getattr(outcome, "args", {}) or {}
    detail = "；".join(f"{k}={v}" for k, v in sorted(args.items()) if k != "detail")
    fallback = key + (f"：{args['detail']}" if "detail" in args else "") \
        + (f"（{detail}）" if detail else "")
    try:
        line = tv.text(tv.msg(key), **args)
    except (KeyError, IndexError) as exc:
        # **缺格式化参数**：模板要 {x} 而构造点没传。回落时至少把已有参数带出来，
        # 不能静默退化成裸键 —— 那正是 `svc.target_required` 只打出一个键名的成因。
        line = f"{fallback}［文案参数缺失：{exc}］"
    except Exception:                              # noqa: BLE001
        line = fallback
    err.write(line + "\n")
    # **`--json` 时失败也要给结构化输出**。此前任何非零码下 stdout 都是
    # 零字节，机读消费者只能靠 rc 猜是什么错 —— 而 `verbs.tsv` 逐动词登记的
    # 码集本身就有偏差。人类文案仍走 stderr（INV-17 的两条流分工不变），
    # 这里只是把机器要的那份补到 stdout。
    if (inv.args or {}).get("json"):
        import json as _json
        out.write(_json.dumps(
            {"kind": "error", "rc": outcome.rc, "key": key,
             "message": line,
             "args": {k: v for k, v in args.items()},
             "committed": list(getattr(outcome, "committed", None) or [])},
            ensure_ascii=False, sort_keys=True, default=str) + "\n")
    committed = getattr(outcome, "committed", None)
    if committed:
        err.write(f"  已落地（需收敛）：{', '.join(committed)}\n")
    return outcome.rc
