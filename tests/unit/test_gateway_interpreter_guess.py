"""C16：`up api --dry-run` 打印的 argv[0] 原本恒为 `/usr/bin/python3` 占位，而真启动在健康安装上
用的是 venv 解释器。预测只看文件（不起子进程，dry-run 零外部命令）：venv 的 python 可执行、
`pyvenv.cfg` 的 home 存在 ⇒ venv；否则系统解释器。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json, os, tempfile
from mlx_server import runtime
d = tempfile.mkdtemp()
py = os.path.join(d, "bin", "python3")
os.makedirs(os.path.dirname(py))
with open(py, "w") as fh:
    fh.write("#!/bin/sh\n")
os.chmod(py, 0o755)
no_cfg = runtime.predict_gateway_interpreter(py)
with open(os.path.join(d, "pyvenv.cfg"), "w") as fh:
    fh.write("home = /usr/bin\nversion = 3.13.1\n")
good = runtime.predict_gateway_interpreter(py)
with open(os.path.join(d, "pyvenv.cfg"), "w") as fh:
    fh.write("home = /nonexistent/python/bin\n")
broken = runtime.predict_gateway_interpreter(py)
missing = runtime.predict_gateway_interpreter(os.path.join(d, "nope"))
print(json.dumps({k: [v["path"] == py, v["source"]] for k, v in
                  (("no_cfg", no_cfg), ("good", good), ("broken", broken), ("missing", missing))}))
"""


def test_dry_run_interpreter_prediction(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["good"] == [True, "venv"], got
    for k in ("no_cfg", "broken", "missing"):
        assert got[k] == [False, "system"], f"{k}：venv 不完整时预测系统解释器：{got}"
