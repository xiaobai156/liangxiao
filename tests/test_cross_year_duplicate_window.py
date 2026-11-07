"""期号跨年重复时，top 窗口必须只认当轮最新的一条。

上官研之、市区幸福的页面保留超过一年历史：同一个期号在页面里出现两次、值不同
（上一轮年度的行）。现在靠 `pick=top` + 固定「前 3 条」窗口兜住。
如果有人把这两站改成 bottom，或把窗口放宽，就会立刻静默取到上一轮的值 ——
这条测试就是钉子：改配置会先在这里红。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from config.loader import load_sites
from parsers.helpers import parse_site_scoped_two_zodiac_records
from parsers.registry import ENGINE_REGISTRY
from validation.direction import select_record


ROOT = Path(__file__).resolve().parents[1]

SOURCE = (
    "上官研之\n"
    "310期 绝杀二肖【鼠兔】 开:00\n"
    "309期 绝杀二肖【虎马】 开:00\n"
    "308期 绝杀二肖【牛羊】 开:00\n"
    "310期 绝杀二肖【龙蛇】 开:00\n"
    "309期 绝杀二肖【猪狗】 开:00\n"
    "308期 绝杀二肖【猴鸡】 开:00\n"
)


def _site(name: str):
    return next(
        site
        for site in load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
        if site.name == name
    )


def test_cross_year_duplicate_periods_only_use_the_newest_round() -> None:
    site = _site("上官研之")
    records = parse_site_scoped_two_zodiac_records(SOURCE, site)

    assert select_record(records, 310, site).zodiac == "鼠兔"

    # 反证：改成 bottom 就会取到上一轮年度的同一期，这正是不许改的原因。
    flipped = replace(site, pick="bottom")
    assert select_record(records, 310, flipped).zodiac == "龙蛇"
