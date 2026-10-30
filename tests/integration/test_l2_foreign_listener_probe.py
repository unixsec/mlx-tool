"""C3：`lsof -t` 看不到其他用户的监听进程（隐含 `-w`、退出 1 且 stderr 为空），代码原本据此判「确知无人」。
2026-10-31 本机实测：launchd 在 127.0.0.1:8021 监听，以普通用户跑 `lsof -iTCP:8021 -t` 返回空。
补一道与属主无关的 connect 探测：连得上而 lsof 没给出 pid ⇒ 有别人在听。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
import socket
from mlx_server import supervisor
from mlx_server.ctx import Ctx, DEFAULT_BINS
lsof = os.path.join(DATA, "lsof_blind")
with open(lsof, "w") as fh:
    fh.write("#!/bin/sh\nexit 1\n")              # 看不到别人的进程时 lsof 就是这样：rc 1、stderr 空
os.chmod(lsof, 0o755)
c = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None, bins=dict(DEFAULT_BINS, lsof=lsof),
        share_root=SHARE)
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
held = supervisor.foreign_listeners(c, port)
srv.close()
free = supervisor.foreign_listeners(c, port)
print(json.dumps({"held": [held.is_unknown(), held.value if not held.is_unknown() else held.kind],
                  "free": [free.is_unknown(), free.value if not free.is_unknown() else free.kind]}))
"""


def test_listener_invisible_to_lsof_is_still_detected(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    unk, val = got["held"]
    assert not unk and val, f"lsof 看不到、但端口确实有人在听 ⇒ 应报外来监听者：{got}"
    assert got["free"] == [False, []], f"真的没人听时照旧判空：{got}"
