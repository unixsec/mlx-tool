"""C14：`config set` 只收 `--value=<v>`，密钥因此出现在 argv 里（同机其他用户 `ps` 可见）与 shell 历史里。
`--value-stdin` 从标准输入读（tty 上不回显）；与 `--value` 互斥（cli.tsv 的 mutex_group 列此前没人执行）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + r"""
import io, sys
from mlx_server import entry
put_settings({})
os.environ.update({"MLX_ROOT_DATA": DATA, "MLX_ROOT_CODE": _ROOT, "MLX_ROOT_WEIGHTS": WEIGHTS,
                   "MLX_SHARE_ROOT": SHARE, "MLX_VENV": VENV, "MLX_GATEWAY_PORT": "8340",
                   "MLX_PORT_RANGE": "8100-8199"})
TOK = "S" * 43
sys.stdin = io.StringIO(TOK + "\n")
o, e = io.StringIO(), io.StringIO()
rc = entry.main(["mlx", "config", "set", "api.token", "--value-stdin"], out=o, err=e)
stored = json.load(open(os.path.join(DATA, "settings.json"))).get("api.token")
o2, e2 = io.StringIO(), io.StringIO()
rc_both = entry.main(["mlx", "config", "set", "api.token", "--value-stdin", "--value=" + TOK], out=o2, err=e2)
o3, e3 = io.StringIO(), io.StringIO()
_real_err, sys.stderr = sys.stderr, e3          # 过程提示与其它 notice 一样走进程 stderr
try:
    rc_argv = entry.main(["mlx", "config", "set", "api.token", "--value=" + "Z" * 43], out=o3, err=e3)
finally:
    sys.stderr = _real_err
print(json.dumps({"rc": rc, "stored": stored == TOK, "leak": TOK in o.getvalue() + e.getvalue(),
                  "both": rc_both, "argv_rc": rc_argv, "warned": "--value-stdin" in e3.getvalue()}))
"""


def test_value_stdin_reads_secret_and_is_exclusive_with_value(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=60)
    assert got["rc"] == 0 and got["stored"] and not got["leak"], got
    assert got["both"] == 2, f"--value-stdin 与 --value 互斥：{got}"
    assert got["argv_rc"] == 0 and got["warned"], f"密钥走 --value 时提示改用 --value-stdin：{got}"
