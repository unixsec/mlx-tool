"""`tv.fixtures` —— **两种合法来源，没有第三种**。

外部 = LS 录制的原始字节（`shapes.json`）；内部 = 由该文件的**唯一写者**渲染。
手写夹具是第三种来源，本模块的存在就是为了让它无处可放。
"""
import base64
import json
from pathlib import Path

# **录制的真实命令输出**（`provenance: recorded`、`sample_stdout_b64`）——
# 它是**测试资产**，不是设计文档，所以跟使用它的代码放在一起 ——
# 放在设计文档目录下时，公开仓库（不含设计文档）里 10 条测试直接 FileNotFoundError。
SHAPES = Path(__file__).resolve().parent / "shapes.json"


class ForbiddenSource(AssertionError):
    """既非录制也非渲染 —— 第三种来源不存在。"""


def recorded(key, channel="stdout"):
    """外部来源：LS 录制的原始字节。"""
    shapes = json.loads(SHAPES.read_text())
    if key not in shapes:
        raise ForbiddenSource(
            f"{key!r} 不在 shapes.json 里 —— 手写夹具是第三种来源，不允许")
    return base64.b64decode(shapes[key].get(f"sample_{channel}_b64", ""))


def rendered(writer, spec):
    """内部来源：由该文件的唯一写者渲染。

    `writer` 必须是**产品模块的函数** —— 测试自己拼一份就是手写夹具。
    """
    mod = getattr(writer, "__module__", "")
    if not mod.startswith("mlx_server") and not mod.startswith("gateway"):
        raise ForbiddenSource(
            f"writer 来自 {mod!r} 而非产品模块 —— 渲染必须由该文件的唯一写者做")
    return writer(spec)


def shape_of(key):
    """夹具的 shape 声明（`byte-exact` 项逐字节，`shape-only` 项按形态）。"""
    shapes = json.loads(SHAPES.read_text())
    e = shapes[key]
    return {"stability": e["stability"], "rc": e["rc"], "shape": e.get("shape", {})}
