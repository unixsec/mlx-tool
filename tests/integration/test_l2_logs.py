"""`mlx logs <服务>`（原「未实现的命令」）：日志最后 N 行原样输出；没有日志 rc 6；--follow 追加新内容。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
import threading, time
from mlx_server.cmd.svc import Invocation, run
os.makedirs(os.path.join(DATA, "logs"), exist_ok=True)
p = os.path.join(DATA, "logs", "qwen.log")
with open(p, "w") as fh:
    fh.write("".join(f"line {i}\n" for i in range(200)))
tail = run(ctx, None, Invocation("logs", "qwen", args={"lines": "3"}), {})
none = run(ctx, None, Invocation("logs", "nosuch"), {})
seen = []
def append():
    time.sleep(0.8)
    with open(p, "a") as fh:
        fh.write("new A\nnew B\n")
    time.sleep(1.2)
    import _thread; _thread.interrupt_main()
threading.Thread(target=append, daemon=True).start()
fol = run(ctx, None, Invocation("logs", "qwen", args={"lines": "1", "follow": True}), {"emit": seen.append})
print(json.dumps({"tail": [tail.rc, tail.payload["text"]], "none": [none.rc, none.msg_key],
                  "follow": [fol.rc, seen]}))
"""


def test_logs_tail_missing_and_follow(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    assert got["tail"] == [0, "line 197\nline 198\nline 199"], got
    assert got["none"] == [6, "logs.none"], got
    rc, seen = got["follow"]
    assert rc == 0 and seen == ["line 199", "new A", "new B"], got
