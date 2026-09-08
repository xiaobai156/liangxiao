from __future__ import annotations

from dataclasses import replace

import pytest

from domain.models import DOCUMENT_BOUNDARY, Site
from parsers.new_sites_241 import (
    BUBU_GAOSHENG_URL,
    JIUJIE_LIANGFENG_URL,
    LUJIU_HOME_URL,
    LUJIU_WENSHA_URL,
    TIANLANG_SHAXING_URL,
    YUEKU_ZHIXIAO_URL,
    parse_bubu_gaosheng_top_records,
    parse_jiujie_liangfeng_top_records,
    parse_lujiu_home_top_records,
    parse_lujiu_wensha_bottom_records,
    parse_tianlang_shaxing_bottom_records,
    parse_yueku_zhixiao_top_records,
)
from parsers.registry import ENGINE_REGISTRY
from services.recent_history import history_result_from_records
from validation.direction import select_record


def site(name: str, pick: str, url: str, parser: str) -> Site:
    return Site(name, pick, url, parser=parser, payload="page_and_scripts")


def records(start: int, end: int, *, semantic: str = "稳杀二肖") -> str:
    return " ".join(
        f"{period:03d}期:{semantic}【牛马】开:0000准"
        for period in range(start, end - 1, -1)
    )


@pytest.mark.parametrize(
    ("parser", "site_config", "source", "current", "expected"),
    [
        (
            parse_lujiu_wensha_bottom_records,
            site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom"),
            "241期：49卢九【稳杀二肖】优秀推荐 "
            + " ".join(
                f"{period:03d}期:稳杀二肖【牛马】开:0000准"
                for period in range(232, 242)
            ),
            241,
            "牛马",
        ),
        (
            parse_lujiu_home_top_records,
            site("卢九", "top", LUJIU_HOME_URL, "lujiu_home_top"),
            "49卢九【稳杀二肖】 " + records(240, 231),
            240,
            "牛马",
        ),
        (
            parse_yueku_zhixiao_top_records,
            site("月窟织绡", "top", YUEKU_ZHIXIAO_URL, "yueku_zhixiao_top"),
            "绝杀料 241期:【绝杀贰肖】创造百万 月窟织绡 发表于 "
            + records(240, 231, semantic="绝杀二肖"),
            240,
            "牛马",
        ),
        (
            parse_jiujie_liangfeng_top_records,
            site("旧街凉风", "top", JIUJIE_LIANGFENG_URL, "jiujie_liangfeng_top"),
            "高手料 241期:旧街凉风【稳杀二肖】 "
            + records(241, 232).replace(":稳杀二肖", ":☆稳杀二肖☆"),
            241,
            "牛马",
        ),
        (
            parse_bubu_gaosheng_top_records,
            site("步步高升", "top", BUBU_GAOSHENG_URL, "bubu_gaosheng_top"),
            "【稳杀二肖】步步高升 " + records(241, 232),
            241,
            "牛马",
        ),
        (
            parse_tianlang_shaxing_bottom_records,
            site("天狼杀星", "bottom", TIANLANG_SHAXING_URL, "tianlang_shaxing_bottom"),
            "精英贴 241期 天狼杀星【绝杀二肖】已上料 "
            + " ".join(
                f"{period:03d}期：→绝杀二肖←【牛马】开0000准"
                for period in range(232, 242)
            ),
            241,
            "牛马",
        ),
    ],
)
def test_new_site_parser_identity_history_and_direction(
    parser, site_config, source, current, expected
) -> None:
    parsed = parser(source, site_config)

    assert select_record(parsed, current, site_config).zodiac == expected
    assert history_result_from_records(site_config, parsed, current).ok
    assert ENGINE_REGISTRY[site_config.parser] is parser
    assert parser(source, replace(site_config, name="错误站名")) == []
    assert (
        parser(
            source,
            replace(site_config, pick="bottom" if site_config.pick == "top" else "top"),
        )
        == []
    )
    assert parser(source, replace(site_config, url=site_config.url + "?wrong=1")) == []
    assert parser(source + " " + source, site_config) == []
    assert parser(source + DOCUMENT_BOUNDARY + source, site_config) == []


def test_lujiu_home_missing_periods_fails_ten_period_history() -> None:
    site_config = site("卢九", "top", LUJIU_HOME_URL, "lujiu_home_top")
    source = "49卢九【稳杀二肖】 " + records(240, 237) + " " + records(234, 231)

    result = history_result_from_records(
        site_config, parse_lujiu_home_top_records(source, site_config), 240
    )

    assert not result.ok
    assert "236,235" in result.error


def test_new_site_parsers_reject_wrong_field_and_duplicate_heading() -> None:
    site_config = site("卢九", "top", LUJIU_HOME_URL, "lujiu_home_top")
    wrong_field = "49卢九【稳杀一尾】 " + records(240, 231, semantic="稳杀一尾")
    duplicated = "49卢九【稳杀二肖】 " + records(240, 231) + " 49卢九【稳杀二肖】"

    assert parse_lujiu_home_top_records(wrong_field, site_config) == []
    assert parse_lujiu_home_top_records(duplicated, site_config) == []
