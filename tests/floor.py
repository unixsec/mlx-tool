"""在 3.9.6 下限解释器里跑一段驱动产品的代码，回传它最后一行打印的 JSON。

conftest 约束逐字：「不得 import 产品模块后在 venv 解释器里直接调用 ——
那测的是另一个解释器」。各测试文件此前各抄一份 `_floor`，新测试统一用这里。
"""
import json
import subprocess
import textwrap


def run_floor(payload, sys_py, prog, with_gateway=False, timeout=30, **subs):
    # type: (object, str, str, bool, int, object) -> object
    text = textwrap.dedent(prog).replace("@PAYLOAD@", str(payload))
    for k, v in subs.items():
        text = text.replace(f"@{k}@", str(v))
    path = f"{payload}:{payload}/lib" if with_gateway else f"{payload}/lib"
    r = subprocess.run([sys_py, "-c", text], capture_output=True, text=True,
                       timeout=timeout, check=False,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": path})
    assert r.returncode == 0, f"下限解释器执行失败：\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])
