"""`tv.stubs` —— 每个外部命令**恰好一份**桩，纯回放。

三条硬约束（03b §1.9）：
  ① **纯回放**：字节来自 `shapes.json` 的录制，桩里不手写输出
  ② **记账 + 留证**：每次调用都进 ledger，供「调用计数 == N」类断言求值
  ③ **未见过的调用形态显式失败** —— 不得静默返回空，那会让缺失的编排静默通过
"""
import base64
import json
import shlex
from pathlib import Path

# **录制的真实命令输出**（`provenance: recorded`、`sample_stdout_b64`）——
# 它是**测试资产**，不是设计文档，所以跟使用它的代码放在一起 ——
# 放在设计文档目录下时，公开仓库（不含设计文档）里 10 条测试直接 FileNotFoundError。
SHAPES = Path(__file__).resolve().parent / "shapes.json"


class UnexpectedCall(AssertionError):
    """桩收到未编排的调用形态。**显式失败**，不静默。"""


class Stub:
    """一个外部命令的桩。

    `bind(command, keys)` 编排一个**有序回放序列**；同一命令在一个用例内
    需要先后返回不同结果时（如 lsof 先 free 后 occupied），靠 `scenario_key` 选路。
    """

    def __init__(self, shapes_path=None):
        self._shapes = json.loads(Path(shapes_path or SHAPES).read_text())
        self._plan = {}      # command → [key, ...]（有序回放）
        self.ledger = []     # [(command, argv, key), ...] 供计数断言

    def bind(self, command, keys):
        """编排某命令的回放序列。keys 是 shapes.json 的键，按调用次序消费。"""
        for k in keys:
            if k not in self._shapes:
                raise KeyError(f"shapes.json 里没有 {k!r} —— 桩只回放录制过的字节")
        self._plan.setdefault(command, []).extend(keys)
        return self

    def call(self, argv):
        """回放一次。返回 (rc, stdout_bytes, stderr_bytes)。"""
        command = argv[0] if isinstance(argv, (list, tuple)) else shlex.split(argv)[0]
        queue = self._plan.get(command)
        if not queue:
            raise UnexpectedCall(
                f"桩收到未编排的调用：{argv!r}\n"
                f"已编排的命令：{sorted(self._plan)}\n"
                "（静默返回空会让缺失的编排通过 —— 所以这里显式失败）"
            )
        key = queue.pop(0)
        e = self._shapes[key]
        self.ledger.append((command, list(argv), key))
        return (e["rc"],
                base64.b64decode(e.get("sample_stdout_b64", "")),
                base64.b64decode(e.get("sample_stderr_b64", "")))

    def count(self, command=None):
        """调用计数。`DE-T-018`「外部命令计数 == 0」这类断言的求值面。"""
        return len([r for r in self.ledger if command is None or r[0] == command])

    def unused(self):
        """编排了却没被消费的 key —— 说明用例的预期与实际路径不符。"""
        return {c: list(q) for c, q in self._plan.items() if q}

    @staticmethod
    def ledger_path(tmpdir):
        """桩的调用流水落点。传给子进程的 `MLX_STUB_LEDGER`。"""
        return str(Path(tmpdir) / "stub_calls.log")

    @staticmethod
    def calls(tmpdir):
        """桩实际被调用的次数与参数。**这是「注入面真的接通了」的唯一证据。**"""
        p = Path(Stub.ledger_path(tmpdir))
        return p.read_text().splitlines() if p.exists() else []

    def as_ps_path(self, tmpdir):
        """生成一个 shell 桩脚本，让**真实子进程**调用也走回放。

        这是 L2 与 L3 的接缝：产品调 `subprocess.run([ps_path, ...])`，
        而 ps_path 指向这个脚本。
        """
        p = Path(tmpdir) / "stub_cmd"
        # **记账**：`$MLX_STUB_LEDGER` 指向一个文件时，每次被调用追加一行。
        # 没有它，「桩被注进去了」这件事无法断言 —— 而这正是
        # `test_l2_dry_run_zero_external_commands` 十几个月来的处境：
        # 它注了 `MLX_BIN_PS`，而产品**从不读那个键**，桩就是摆设，
        # 用例实际跑在真 `/bin/ps` 上。注入面修好之后若不补这条断言，
        # 「以前没生效」这件事仍然看不出来。
        lines = ["#!/bin/bash",
                 '[ -n "$MLX_STUB_LEDGER" ] && echo "$MLX_STUB_KEY|$*" >> "$MLX_STUB_LEDGER"',
                 "case \"$MLX_STUB_KEY\" in"]
        for key, e in self._shapes.items():
            out = base64.b64decode(e.get("sample_stdout_b64", "")).decode(errors="replace")
            err = base64.b64decode(e.get("sample_stderr_b64", "")).decode(errors="replace")
            lines += [f"  {shlex.quote(key)})",
                      f"    printf %s {shlex.quote(out)}",
                      f"    printf %s {shlex.quote(err)} >&2",
                      f"    exit {e['rc']} ;;"]
        lines += ["  *) echo \"stub: 未编排的 key: $MLX_STUB_KEY\" >&2; exit 97 ;;", "esac"]
        p.write_text("\n".join(lines) + "\n")
        p.chmod(0o755)
        return str(p)
