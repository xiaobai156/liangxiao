from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[1]
TARGET_NAMES = ("探讨资料",)


def configured_targets():
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    return [site for site in sites if site.name in TARGET_NAMES]


def source_for(site) -> str:
    rows = {
        "探讨资料": (
            "180期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【兔.羊】开: 狗21 准上榜高手",
            "181期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【龙.羊】开: 鼠19 准上榜高手",
            "184期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【狗.猴】开: 马01 准上榜高手",
            "185期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【蛇.羊】开: 羊36 错",
            "186期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【牛.蛇】开: 猴23 准上榜高手",
            "本资料最早发表在836386.com欢迎转发+关注",
            "212期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【蛇.鼠】开: 牛06 准上榜高手",
            "213期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【狗.猴】开: 猴35 错",
            "214期: 🤘 『探讨资料』 🤘 绝杀②肖 🤘【鸡.马】开: 00准",
        ),
    }
    heading = f"214期：{site.name}【绝杀二肖】"
    return "<h1>" + heading + "</h1><div class='divbody'>" + "<p>".join(rows[site.name]) + "<p>下一贴：其他资料</div>"


@pytest.mark.parametrize("site_name", TARGET_NAMES)
def test_target_sites_keep_the_same_post_until_next_post(site_name: str) -> None:
    site = next(site for site in configured_targets() if site.name == site_name)
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source_for(site), site)
    window = direction_window(records, site)
    selected = select_record(records, 213, site)

    assert site.stop == r"\s*下一贴\s*[：:]"
    assert [record.period for record in window] == [212, 213, 214]
    assert selected.period == 213
    assert selected.zodiac == "狗猴"

    with pytest.raises(ValueError, match="bottom 候选内未找到 211 期"):
        select_record(records, 211, site)
