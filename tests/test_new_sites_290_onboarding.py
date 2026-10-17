from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import Site
from parsers.helpers import parse_site_scoped_two_zodiac_records
from parsers.registry import ENGINE_REGISTRY
from validation.direction import select_record


ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "千羽制作": {
        "pick": "top",
        "url": "https://zvnarl.zo7b8-cg55k-zautjw.work:17477/topic/219979.html",
        "parser": "site_scoped_two_zodiac",
        "payload": "page_and_scripts",
        "title": "千羽制作",
    },
    "同出一辙": {
        "pick": "top",
        "url": "https://kplsux.v2ubt-ak9tc-ryjuwe.cyou:17477/topic/292706.html",
        "parser": "site_scoped_two_zodiac",
        "payload": "page_and_scripts",
        "title": "同出一辙",
    },
    "春暧花开": {
        "pick": "bottom",
        "url": "https://dtnjpph.1p0w8-q5dks-oxbahg.xyz:16677/topic/450438.html",
        "parser": "site_scoped_two_zodiac",
        "payload": "page_and_scripts",
        "title": "春暧花开",
    },
    "笑容可掬": {
        "pick": "bottom",
        "url": "https://dtnjpph.1p0w8-q5dks-oxbahg.xyz:16677/topic/450411.html",
        "parser": "site_scoped_two_zodiac",
        "payload": "page_and_scripts",
        "title": "笑容可掬",
    },
}

QIANYU_SOURCE = (
    "千羽制作\n"
    "290期:【千羽制作】 【绝杀二肖】 作者:千羽制作\n"
    "290期:【千羽制作】【绝杀二肖】【马羊】开:00\n"
    "289期:【千羽制作】【绝杀二肖】【鸡狗】开:鼠38\n"
    "288期:【千羽制作】【绝杀二肖】【马兔】开:猴24\n"
    "287期:【千羽制作】【绝杀二肖】【猪鸡】开:狗17\n"
)
QIANYU_SECOND_DATASET = (
    "365期:【千羽制作】 【绝杀二肖】 作者:千羽制作\n"
    "365期:【千羽制作】【绝杀二肖】【鼠牛】开:00\n"
    "364期:【千羽制作】【绝杀二肖】【虎兔】开:00\n"
    "290期:【千羽制作】【绝杀二肖】【龙兔】开:00\n"
)
TONGCHU_SOURCE = (
    "高手贴 290期 【绝杀二肖】 已公开 作者:同出一辙\n"
    "290期:绝杀二肖【羊马】开00对\n"
    "289期:绝杀二肖【猴羊】开蛇38对\n"
    "288期:绝杀二肖【猪狗】开鼠25对\n"
    "287期:绝杀二肖【猪牛】开虎11对\n"
)
CHUNNUAN_SOURCE = (
    "主题内容--722259d.com\n"
    "春暧花开\n"
    "288期:【 绝杀二肖特 】【猪鸡】开：狗13准\n"
    "289期:【 绝杀二肖特 】【马虎】开：0000准\n"
    "290期:【 绝杀二肖特 】【牛兔】开：0000准\n"
)
XIAORONG_EARLY_TABLE = "".join(
    f"{period}期:【杀二肖】【牛马】开：龙15准\n" for period in range(295, 290, -1)
)
XIAORONG_SOURCE = (
    "笑容可掬\n"
    + XIAORONG_EARLY_TABLE
    + "".join(
        f"{period}期:【杀二肖】【{zodiac}】开：{opened}准\n"
        for period, zodiac, opened in (
            (288, "牛鼠", "马25"),
            (289, "虎鸡", "蛇38"),
            (290, "蛇牛", "0000"),
        )
    )
)


def _live_sites() -> dict[str, Site]:
    return {
        site.name: site
        for site in load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    }


def test_new_sites_290_are_registered_in_live_config() -> None:
    sites = _live_sites()

    for name, expected in EXPECTED.items():
        site = sites[name]
        assert site.pick == expected["pick"]
        assert site.url == expected["url"]
        assert site.parser == expected["parser"]
        assert site.payload == expected["payload"]
        assert site.title == expected["title"]
        assert ENGINE_REGISTRY[site.parser] is parse_site_scoped_two_zodiac_records


def test_new_sites_290_cache_seed_has_ten_periods_with_evidence() -> None:
    cache = json.loads((ROOT / "recent_10_cache.json").read_text(encoding="utf-8-sig"))
    entries = {entry["name"]: entry for entry in cache["sites"]}

    window = [str(period) for period in cache["issues"]]
    assert len(window) == 10

    for name in EXPECTED:
        entry = entries[name]
        assert entry["pick"] == EXPECTED[name]["pick"]
        assert sorted(entry["values"], key=int, reverse=True) == window
        assert sorted(entry["positions"], key=int, reverse=True) == window
        assert all(entry["values"][period] for period in window)
        for record in entry["records"]:
            assert record["evidence"], f"{name} {record['period']} 期缺少证据"
            assert record["evidence"][0]["document_url"].startswith("https://")
        assert "status" not in entry and "error" not in entry


@pytest.mark.parametrize(
    ("name", "source", "period", "expected"),
    [
        ("千羽制作", QIANYU_SOURCE, 290, "马羊"),
        ("同出一辙", TONGCHU_SOURCE, 290, "羊马"),
        ("春暧花开", CHUNNUAN_SOURCE, 290, "牛兔"),
        ("春暧花开", CHUNNUAN_SOURCE, 288, "猪鸡"),
        ("笑容可掬", XIAORONG_SOURCE, 290, "蛇牛"),
        ("笑容可掬", XIAORONG_SOURCE, 289, "虎鸡"),
        ("笑容可掬", XIAORONG_SOURCE, 288, "牛鼠"),
    ],
)
def test_new_sites_290_parser_selects_the_configured_direction(
    name: str, source: str, period: int, expected: str
) -> None:
    site = _live_sites()[name]

    records = parse_site_scoped_two_zodiac_records(source, site)

    assert select_record(records, period, site).zodiac == expected


def test_qianyu_second_dataset_must_not_displace_the_first_window() -> None:
    site = _live_sites()["千羽制作"]
    source = QIANYU_SOURCE + QIANYU_SECOND_DATASET + QIANYU_SECOND_DATASET.replace(
        "365期", "290期"
    )

    records = parse_site_scoped_two_zodiac_records(source, site)
    periods = [(record.period, record.zodiac) for record in records if record.period == 290]

    assert periods[:2] == [(290, "马羊"), (290, "龙兔")]
    assert select_record(records, 290, site).zodiac == "马羊"


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("千羽制作", QIANYU_SOURCE),
        ("同出一辙", TONGCHU_SOURCE),
        ("春暧花开", CHUNNUAN_SOURCE),
        ("笑容可掬", XIAORONG_SOURCE),
    ],
)
def test_new_sites_290_reject_an_unknown_column_title(name: str, source: str) -> None:
    site = _live_sites()[name]

    assert parse_site_scoped_two_zodiac_records(source, replace(site, title="错误栏目")) == []


def test_xiaorong_keyi_early_duplicate_table_stays_out_of_the_bottom_window() -> None:
    site = _live_sites()["笑容可掬"]

    records = parse_site_scoped_two_zodiac_records(XIAORONG_SOURCE, site)
    early = [record for record in records if record.period > 290]

    assert [record.period for record in early] == [295, 294, 293, 292, 291]
    assert select_record(records, 290, site).zodiac == "蛇牛"
    with pytest.raises(ValueError, match="bottom 候选内未找到 291 期"):
        select_record(records, 291, site)
