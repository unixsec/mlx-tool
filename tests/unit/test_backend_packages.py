"""spec D3：mlx_vlm 由安装器默认装、缺失时拒绝（P1）—— 因此它是**必需**包。"""
import sys


def test_mlx_vlm_is_required_not_optional(payload):
    sys.path.insert(0, str(payload / "lib"))
    from mlx_server.caps import OPTIONAL, REQUIRED  # noqa: PLC0415
    assert "mlx_vlm" in REQUIRED, "多模态模型经判定路由到 mlx_vlm，它不能是可选包"
    assert "mlx_vlm" not in OPTIONAL
