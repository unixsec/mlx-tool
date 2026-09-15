"""网络夹具是冻结的真实抓取（脱敏）。测试开头先确认夹具含 spec §8.2 要求的全部形态 ——
夹具缺某种形态时，依赖它的规则测试会空真通过。"""
import hashlib
from pathlib import Path

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"


def test_hashes_match():
    want = {}
    for line in (NET / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        want[name] = digest
    assert sorted(want) == ["ifconfig.txt", "networksetup.txt"]
    for name, digest in want.items():
        got = hashlib.sha256((NET / name).read_bytes()).hexdigest()
        assert got == digest, f"{name} 与 SHA256SUMS 不符 —— 夹具被改过"


def test_fixture_contains_required_shapes():
    hw = (NET / "networksetup.txt").read_text()
    ifc = (NET / "ifconfig.txt").read_text()
    for s in ("Hardware Port: Wi-Fi", "Hardware Port: Ethernet Adapter (en3)",
              "Hardware Port: Thunderbolt Bridge", "Hardware Port: Thunderbolt 1"):
        assert s in hw, f"networksetup 夹具缺「{s}」"
    for s in ("bridge0: flags=8863<UP,", "utun4: flags=", "inet 10.14.0.2 --> 10.14.0.2",
              "%en0 prefixlen", "%bridge100 prefixlen", "member: vmenet0",
              "member: en1", "inet 172.20.10.3 netmask 0xfffffff0",
              "inet 10.211.55.2 netmask 0xffffff00"):
        assert s in ifc, f"ifconfig 夹具缺「{s}」"
    assert "\t" in ifc, "ifconfig 夹具里的制表符丢了（复制时被转成了空格）"
