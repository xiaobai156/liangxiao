from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

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
from parsers.registry import ENGINE_REGISTRY, _mask_invalid_periods
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
            + records(241, 232, semantic="绝杀二肖"),
            241,
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


def _wensha_line(period: int, zodiac: str) -> str:
    return f"{period}期: 稳杀二肖 【 {zodiac} 】开: 马25 准 "


WENSHA_HEADING = "我期：49卢九【稳杀二肖】优秀推荐 "
WENSHA_AD = " 鸿蒙娱乐城APP 授权：免费软件 大小：16.98MB "


def test_lujiu_wensha_block_stops_at_ad_area() -> None:
    """记录段之后的广告区不得并入；广告区里同格式的行必须被截掉。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + WENSHA_AD + _wensha_line(999, "龙虎"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 294, 295]
    assert [r.zodiac for r in records_] == ["鼠马", "虎羊", "兔狗"]


def test_lujiu_wensha_block_rejects_cross_block_slide() -> None:
    """块后出现另一段同格式记录时，方向反转即截断，不能滑到别的块取值。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    slide = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
             + _wensha_line(295, "兔狗") + " 其他内容 "
             + _wensha_line(80, "鸡狗") + _wensha_line(81, "猪鼠") + _wensha_line(82, "牛兔"))

    records_ = parse_lujiu_wensha_bottom_records(slide, site_config)

    assert [r.period for r in records_] == [293, 294, 295]
    assert select_record(records_, 295, site_config).zodiac == "兔狗"


def test_lujiu_wensha_block_keeps_historical_gaps() -> None:
    """历史缺期但方向一致时必须保留，不能因缺期被误截断。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    gapped = (WENSHA_HEADING + _wensha_line(290, "猪牛") + _wensha_line(292, "羊虎")
              + _wensha_line(295, "兔狗") + WENSHA_AD)

    records_ = parse_lujiu_wensha_bottom_records(gapped, site_config)

    assert [r.period for r in records_] == [290, 292, 295]


# --- 期号后缀「期/起」写法与 1-365 校验 -------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sites.json"


def _configured_record(name: str) -> str:
    """从正式配置读取站点正则，确保测的是线上真正生效的那一条。"""
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return next(entry["record"] for entry in config if entry["name"] == name)


def _zuimei_pattern() -> re.Pattern[str]:
    return re.compile(_configured_record("最美记忆"))


@pytest.mark.parametrize(
    ("text", "period", "zodiac"),
    [
        ("296起:￥精杀二肖￥【猪羊】开:0000准", 296, "猪羊"),
        ("295期:￥精杀二肖￥【龙蛇】开:马25准", 295, "龙蛇"),
        ("296期起:￥精杀二肖￥【猪羊】开:0000准", 296, "猪羊"),
    ],
)
def test_period_suffix_accepts_qi_variants(text: str, period: int, zodiac: str) -> None:
    """「期」「起」「期起」三种后缀都必须能正确取到目标期与取值（用线上配置正则）。"""
    match = _zuimei_pattern().search(text)

    assert match is not None
    assert int(match.group("period")) == period
    assert match.group("zodiac") == zodiac


@pytest.mark.parametrize(
    "text",
    [
        "1296起:￥精杀二肖￥【猪羊】开:0000准",
        "自2026起:￥精杀二肖￥【猪羊】开:0000准",
    ],
)
def test_period_suffix_rejects_digits_before_period(text: str) -> None:
    """期号前面紧跟数字时不得被当成 3 位期号（1296起 不能读成 296 期）。"""
    assert _zuimei_pattern().search(text) is None


def test_configured_record_requires_line_start_anchor() -> None:
    """期号必须位于记录行开头，标题行内嵌的期号不得被当成数据行。"""
    pattern = _zuimei_pattern()

    # 标题行里的期号紧贴其它文字 -> 不得匹配
    assert pattern.search("296期【精杀二肖】已公开297期:￥精杀二肖￥【猪羊】开:0000准") is None
    # 独立成行 -> 必须匹配
    match = pattern.search("296期【精杀二肖】已公开\n297期:￥精杀二肖￥【猪羊】开:0000准")
    assert match is not None
    assert int(match.group("period")) == 297


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("296起", "296起"),
        ("365起", "365起"),
        ("366起", "   起"),
        ("000起", "   起"),
        ("1296起", "    起"),
        ("自2026起", "自    起"),
    ],
)
def test_mask_invalid_periods_covers_qi_suffix(text: str, expected: str) -> None:
    """掩码层必须把「起」也纳入三位数与 1-365 校验（精确比对，不用弱断言）。"""
    assert _mask_invalid_periods(text) == expected


def test_mask_invalid_periods_leaves_ordinary_words_alone() -> None:
    """掩码只针对「数字+期/起」，普通词语（起早摸黑、东山再起）不得受影响。"""
    for text in ("起早摸黑", "东山再起", "一起加油", "充值500起"):
        masked = _mask_invalid_periods(text)
        # 非三位期号的数字+起 允许被掩码，但纯文字必须原样保留
        if not re.search(r"\d", text):
            assert masked == text


def test_missing_target_period_fails_instead_of_borrowing_neighbour() -> None:
    """目标期缺失必须失败，不得借用相邻期的值（跨期借值红线）。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + WENSHA_AD)

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 294, 295]
    with pytest.raises(ValueError, match="未找到 296 期"):
        select_record(records_, 296, site_config)


# --- 区块结束边界：跨块滑动、1-2 条块、重复行、跨年回绕 ----------------------


def test_block_rejects_far_neighbour_block_by_heading_anchor() -> None:
    """远距离邻块必须被标题期号锚定拒绝。

    标题写 298，但数据块被截断到 295（邻块 100-102 间隔超限被切掉），
    块内不含 298 -> 判失败，不返回候选。
    """
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = ("298期：49卢九【稳杀二肖】优秀推荐 "
              + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + " 别的栏目 "
              + _wensha_line(100, "鸡狗") + _wensha_line(101, "猪鼠") + _wensha_line(102, "牛兔"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert records_ == []
    with pytest.raises(ValueError):
        select_record(records_, 298, site_config)


def test_block_accepts_complete_block_containing_heading_period() -> None:
    """正常完整块（含标题期号）必须正常放行，锚定不能误杀。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = ("298期：49卢九【稳杀二肖】优秀推荐 "
              + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + _wensha_line(296, "鸡狗")
              + _wensha_line(297, "猪鼠") + _wensha_line(298, "牛兔"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 294, 295, 296, 297, 298]
    assert select_record(records_, 298, site_config).zodiac == "牛兔"


def test_window_distance_guard_allows_normal_nearby_period() -> None:
    """目标期在窗口距离内时必须正常放行，不能过度拦截。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + WENSHA_AD)

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert select_record(records_, 295, site_config).zodiac == "兔狗"
    assert select_record(records_, 294, site_config).zodiac == "虎羊"


def test_block_with_two_records_does_not_skip_validation() -> None:
    """块内只有 1-2 条记录时不得直接放行（旧实现 len<3 直接 return）。

    单条 295 后紧跟降序邻块 293/292/291：方向基准一旦被邻块带偏，
    就会把整段邻块并进来。必须靠期数间隔/窗口上限挡住。
    """
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(295, "兔狗") + " 别的栏目 "
              + _wensha_line(200, "鼠马") + _wensha_line(199, "羊虎") + _wensha_line(198, "牛龙"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    # 295 -> 200 的间隔超过 MAX_PERIOD_GAP，必须截断在 295
    assert [r.period for r in records_] == [295]


def test_block_truncates_when_neighbour_gap_too_large() -> None:
    """邻块与当前块间隔过大时必须截断（间隔即跨块信号）。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊")
              + _wensha_line(295, "兔狗") + " 别的栏目 "
              + _wensha_line(180, "鸡狗") + _wensha_line(181, "猪鼠") + _wensha_line(182, "牛兔"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 294, 295]


def test_block_treats_duplicate_first_period_as_same_line() -> None:
    """首两行同期数（重复展示行）不得把方向基准算反。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + _wensha_line(293, "鼠马") + _wensha_line(293, "鼠马")
              + _wensha_line(294, "虎羊") + _wensha_line(295, "兔狗") + WENSHA_AD)

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 293, 294, 295]


def test_block_handles_year_wrap_365_to_001() -> None:
    """365 -> 001 属正常跨年回绕，不得被当成方向反转截断。

    真实页面（天狼杀星 298 期）标题写 298、数据尾部是 298，历史段含 365->001。
    这里标题用 002（数据末条），确保锚点校验不会因跨年误判。
    """
    site_config = site("天狼杀星", "bottom", TIANLANG_SHAXING_URL, "tianlang_shaxing_bottom")
    heading = "精英贴 002期 天狼杀星【绝杀二肖】已上料 "

    def line(period: str, zodiac: str) -> str:
        return f"{period}期：→绝杀二肖←【{zodiac}】开蛇13准 "

    source = (heading + line("363", "猴羊") + line("364", "龙兔") + line("365", "鼠猪")
              + line("001", "狗鸡") + line("002", "鼠猪"))

    records_ = parse_tianlang_shaxing_bottom_records(source, site_config)

    assert [r.period for r in records_] == [363, 364, 365, 1, 2]


def test_block_stop_pattern_before_records_does_not_empty_block() -> None:
    """广告文案出现在记录段之前时，不能把整段截空（旧实现会返回 []）。"""
    site_config = site("卢九稳杀", "bottom", LUJIU_WENSHA_URL, "lujiu_wensha_bottom")
    source = (WENSHA_HEADING + " 鸿蒙娱乐城APP 授权：免费软件 "
              + _wensha_line(293, "鼠马") + _wensha_line(294, "虎羊") + _wensha_line(295, "兔狗"))

    records_ = parse_lujiu_wensha_bottom_records(source, site_config)

    assert [r.period for r in records_] == [293, 294, 295]


def test_all_new_241_parsers_have_direction_break_guard() -> None:
    """同模块 6 个解析器都必须启用方向截断，否则同类跨块借值仍在。"""
    import inspect

    from parsers import new_sites_241

    for func_name in (
        "parse_lujiu_wensha_bottom_records",
        "parse_lujiu_home_top_records",
        "parse_yueku_zhixiao_top_records",
        "parse_jiujie_liangfeng_top_records",
        "parse_bubu_gaosheng_top_records",
        "parse_tianlang_shaxing_bottom_records",
    ):
        source_code = inspect.getsource(getattr(new_sites_241, func_name))
        assert "stop_at_direction_break=True" in source_code, f"{func_name} 缺少方向截断"
