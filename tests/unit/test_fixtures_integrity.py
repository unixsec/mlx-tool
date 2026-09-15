"""夹具与 SHA256SUMS 一致；本机存在原始权重时与原文件逐字节一致。"""
import hashlib
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"
ORIGIN = {
    "qwen38": Path.home() / "lab/models/mlx/hub/models--mlx-community--Qwen3.8-27B-8bit/snapshots/main",
    "qwen38-mtp": Path.home() / "lab/models/mlx/hub/models--mlx-community--Qwen3.8-27B-MTP-4bit/snapshots/main",
}


def test_fixture_hashes_match_manifest():
    lines = [ln.split() for ln in (FIX / "SHA256SUMS").read_text().splitlines() if ln.strip()]
    assert len(lines) == 3, f"SHA256SUMS 应有 3 行，实得 {len(lines)}"
    for digest, rel in lines:
        got = hashlib.sha256((FIX / rel).read_bytes()).hexdigest()
        assert got == digest, f"{rel} 与 SHA256SUMS 不符 —— 夹具被改过"
        origin = ORIGIN[rel.split("/")[0]] / rel.split("/")[1]
        if origin.exists():
            assert origin.read_bytes() == (FIX / rel).read_bytes(), f"{rel} 与本机原始权重不一致"
