"""开发环境自检：测试依赖齐全，后面的测试才有意义。缺依赖时明确失败，不跳过。"""
import importlib.util
import sys


def test_dev_venv_has_jinja2_for_template_renderer_tests():
    assert importlib.util.find_spec("jinja2") is not None, (
        f"{sys.executable} 里没有 jinja2 —— 模板渲染相关测试会失败。"
        "补装：.venv/bin/python3 -m pip install -r requirements-dev.txt")
