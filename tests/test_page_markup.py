"""The page's markup: every (i) button finds its own text, and no two elements share an id."""

import re
from collections import Counter

from edmultitool.server import WEB_DIR

PAGE = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_no_two_elements_share_an_id() -> None:
    # a drawn icon and an (i) text once shared a name: the (i) bubble then showed the icon, i.e. nothing
    ids = Counter(re.findall(r'\sid="([^"]+)"', PAGE))
    assert [i for i, n in ids.items() if n > 1] == []


def test_every_info_button_names_a_text_of_its_own() -> None:
    texts = set(re.findall(r'<div id="([^"]+)" class="info-text" hidden>', PAGE))
    wanted = set(re.findall(r'data-info="([^"]+)"', PAGE))
    assert wanted
    assert wanted <= texts
