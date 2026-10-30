"""`tv.sandbox` —— 隔离的文件系统面 + 快照差集。

`DE-T-018` 这类「全轮 diff == ∅」断言的求值面。
"""
import os
from pathlib import Path


class Sandbox:
    def __init__(self, root):
        self.root = Path(root)
        for d in ("code", "data", "weights", "share", "run", "logs"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        # 装好的系统总有后端密钥（安装器 `bootstrap keys`）；1.2.0 起 mlx_lm 后端也要它
        (self.root / "data" / "settings.json").write_text('{"backend.api_key": "' + "B" * 43 + '"}')

    def write_constants(self, **extra):
        """造 `share/constants.env`，并**拷入真实的 `*.tsv` 表**。

        表不手工造：沙箱里少一张表时，产品在这里红而在真实安装下正常，
        或者反过来 —— 手工表的**形状**与产物不同，测试全绿而用户那里崩。
        表是产物的一部分，夹具就该用产物本身。
        """
        import shutil
        real = Path(__file__).resolve().parents[2] / "payload" / "share"
        for tsv in sorted(real.glob("*.tsv")):
            shutil.copy2(tsv, self.root / "share" / tsv.name)
        vals = {
            "MLX_ROOT_CODE": self.root / "code",
            "MLX_ROOT_DATA": self.root / "data",
            "MLX_ROOT_WEIGHTS": self.root / "weights",
            "MLX_GATEWAY_PORT": 8080,
            "MLX_PORT_RANGE": "8100-8199",
        }
        vals.update(extra)
        (self.root / "share" / "constants.env").write_text(
            "".join(f"{k}={v}\n" for k, v in vals.items()))
        return self

    def with_model(self, mid="qwen", repo="x/y", port=8100, revision="main"):
        """登记一个模型 **并把权重真的落到盘上**（委托 `seed_model`）。"""
        seed_model(self.root / "data", self.root / "weights", mid, repo, port, revision)
        return self

    def snapshot(self):
        """全树快照（路径 + 大小 + mtime），供对称差比较。"""
        out = {}
        for dirpath, _, files in os.walk(self.root):
            for f in files:
                p = Path(dirpath) / f
                st = p.stat()
                out[str(p.relative_to(self.root))] = (st.st_size, int(st.st_mtime))
        return out

    @staticmethod
    def diff(before, after):
        """对称差。返回 (新增, 删除, 改动)。"""
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
        return added, removed, changed

    def env(self, payload):
        """产品子进程的环境。"""
        return {
            "PATH": "/usr/bin:/bin",
            "MLX_CODE_ROOT": str(payload),
            "MLX_SHARE_ROOT": str(self.root / "share"),
        }


def seed_model(data_dir, weights_dir, mid="qwen", repo="x/y", port=8100, revision="main"):
    """往一份安装产物里塞一个**可启动**的模型：注册表一行 + 盘上的权重。

    `up` 的校验段第 2/3 步读注册表与权重（`BCR-R-27` 的顺序），
    少了任何一半都会在到达被测行为之前就被拒。此前多处夹具只建空的三根，
    于是那两步实现成空桩也全绿 —— 缺陷因此活了整整一版，症状是模型被起在
    默认端口 8100、别名目录里空无一物、后端把短名当 HF 仓库名去网上查。
    """
    import json
    data_dir, weights_dir = Path(data_dir), Path(weights_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    rec = {"id": mid, "user": {"repo": repo},
           "machine": {"port": port, "revision": revision}}
    with (data_dir / "models.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    snap = (weights_dir / "hub" / ("models--" + repo.replace("/", "--"))
            / "snapshots" / revision)
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text("{}")
    return snap
