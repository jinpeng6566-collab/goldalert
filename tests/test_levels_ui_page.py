"""设置页的页面脚本别在加载时就坏掉（坏掉的话所有按钮都会没反应）。"""

from __future__ import annotations

import re

from goldalert.levels_ui import HTML


def test_page_has_single_script_block():
    assert len(re.findall(r"<script[^>]*>", HTML)) == 1


def test_every_button_handler_is_defined():
    """页面上的 onclick 必须都能在脚本里找到同名函数，否则点了没反应。"""
    handlers = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\(', HTML))
    defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", HTML))
    assert handlers, "页面上应该至少有一个按钮"
    assert handlers <= defined, f"这些按钮没有对应函数: {sorted(handlers - defined)}"


def test_script_only_touches_existing_elements():
    """脚本里 getElementById 取的 id 必须真的存在于页面里，否则一加载就报错。"""
    used = set(re.findall(r"getElementById\('([^']+)'\)", HTML))
    defined = set(re.findall(r'id="([^"]+)"', HTML))
    assert used <= defined, f"页面上找不到这些 id: {sorted(used - defined)}"


def test_no_backslash_quote_in_page_source():
    r"""HTML 里不能出现 \' —— Python 会把反斜杠吃掉，生成出语法错误的脚本。"""
    assert "\\'" not in HTML