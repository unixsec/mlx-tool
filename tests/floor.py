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


# 在下限解释器程序里搭一个**真**安装世界：真表（拷自 payload/share）、真数据根、
# 真权重布局、可选的 venv 解释器包装脚本。任何 up / 判定类测试都从这里起步，
# 不传 ctx=None、不留空权重目录（`empty-stub-hides-behind-null-fixture`）。
WORLD = r'''
import json, os, shutil, stat, tempfile
from mlx_server.ctx import Ctx
_ROOT = tempfile.mkdtemp()
SHARE = os.path.join(_ROOT, "share")
os.makedirs(SHARE)
for _f in os.listdir("@PAYLOAD@/share"):
    if _f.endswith(".tsv"):
        shutil.copy2(os.path.join("@PAYLOAD@/share", _f), SHARE)
DATA = os.path.join(_ROOT, "data")
WEIGHTS = os.path.join(_ROOT, "weights")
VENV = os.path.join(_ROOT, "venv")
for _d in (DATA, WEIGHTS, os.path.join(VENV, "bin")):
    os.makedirs(_d, exist_ok=True)
if "@RENDER_PY@" not in ("", "@" + "RENDER_PY@"):
    # 包装脚本而不是符号链接：venv 的 python 经符号链接调起时找不到 pyvenv.cfg，
    # site-packages（jinja2）会丢
    _w = os.path.join(VENV, "bin", "python3")
    with open(_w, "w") as _fh:
        _fh.write('#!/bin/sh\nexec "@RENDER_PY@" "$@"\n')
    os.chmod(_w, 0o755)
ctx = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None, share_root=SHARE)

def fixture(name):
    with open(os.path.join("@FIXTURES@", name), encoding="utf-8") as fh:
        return fh.read()

def seed(mid, repo, port, config=None, template=None, user=None, machine=None):
    snap = os.path.join(WEIGHTS, "hub", "models--" + repo.replace("/", "--"),
                        "snapshots", "main")
    os.makedirs(snap, exist_ok=True)
    if config is not None:
        with open(os.path.join(snap, "config.json"), "w") as fh:
            fh.write(config if isinstance(config, str) else json.dumps(config))
    if template is not None:
        with open(os.path.join(snap, "chat_template.jinja"), "w") as fh:
            fh.write(template)
    with open(os.path.join(snap, "model.safetensors"), "wb") as fh:
        fh.write(b"\0" * 4096)
    rec = {"id": mid, "user": dict({"repo": repo}, **(user or {})),
           "machine": dict({"port": port, "revision": "main", "size_mb": 1.0},
                           **(machine or {}))}
    with open(os.path.join(DATA, "models.jsonl"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return snap

def put_settings(d):
    with open(os.path.join(DATA, "settings.json"), "w") as fh:
        fh.write(json.dumps(d))

def put_caps(packages):
    with open(os.path.join(SHARE, "caps.json"), "w") as fh:
        fh.write(json.dumps({"fingerprint": "{}", "packages": packages,
                             "collected_at": "t"}))
'''
