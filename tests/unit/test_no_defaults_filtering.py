"""spec §2.2：过滤一律读 schema.tsv 的 keydefs，**任何模块**不得以 `in (_)schema.DEFAULTS` 做成员过滤。"""
import ast


def test_no_membership_filter_against_hardcoded_defaults(payload):
    bad = []
    for f in sorted((payload / "lib" / "mlx_server").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if not isinstance(node, ast.Compare):
                continue
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, ast.Attribute) \
                        and comp.attr == "DEFAULTS" \
                        and getattr(comp.value, "id", None) in ("schema", "_schema"):
                    bad.append(f"{f.relative_to(payload)}:{node.lineno}")
    assert bad == [], f"这些地方拿硬编码 DEFAULTS 做成员过滤，表里的新键会被静默丢弃：{bad}"
