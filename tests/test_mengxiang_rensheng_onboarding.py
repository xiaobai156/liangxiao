from __future__ import annotations

import pytest

from domain.models import DOCUMENT_BOUNDARY, Site
from parsers.dynamic import parse_mengxiang_rensheng_manager_article_records
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from validation.direction import select_record
from validation.records import validate_selected_record


URL = (
    "https://nwrkkmv.rx287-rkrai-jsjccc.xyz:29499/article/manager/"
    "6a55eceff447e21b02daa681?url=ggz"
)
API_URL = (
    "https://nwrkkmv.rx287-rkrai-jsjccc.xyz:29499/"
    "api/proxy/landing-page-data?url=ggz"
)


def site(
    *,
    name: str = "梦想人生",
    pick: str = "bottom",
    url: str = URL,
    payload: str = "admin_article_api",
) -> Site:
    return Site(
        name,
        pick,
        url,
        parser="mengxiang_rensheng_manager_article",
        title="梦想人生",
        payload=payload,
        api_url=API_URL,
    )


def source(*rows: tuple[int, str, str]) -> str:
    return "".join(
        f"<p>{period:03d}期：《梦想人生》绝杀二肖"
        f"【{zodiac}】开：{open_result}</p>"
        for period, zodiac, open_result in rows
    )


HISTORY = (
    (221, "猴马", "马01准"),
    (222, "马牛", "蛇26准"),
    (223, "牛鼠", "猴23准"),
    (224, "马蛇", "狗09准"),
    (225, "虎马", "马01错"),
    (226, "龙牛", "虎17准"),
    (227, "鸡蛇", "000准"),
    (228, "猴羊", "000准"),
    (229, "狗猪", "000准"),
    (230, "龙兔", "000准"),
)


def test_parses_history_and_selects_230_from_bottom_window() -> None:
    records = parse_mengxiang_rensheng_manager_article_records(source(*HISTORY), site())

    assert [(record.period, record.zodiac) for record in records] == [
        (period, zodiac) for period, zodiac, _open_result in HISTORY
    ]
    selected = select_record(records, 230, site())
    assert selected.zodiac == "龙兔"
    assert validate_selected_record(selected, 230)


def test_parser_is_registered_for_the_exact_site_identity() -> None:
    target = site()

    assert (
        ENGINE_REGISTRY["mengxiang_rensheng_manager_article"]
        is parse_mengxiang_rensheng_manager_article_records
    )
    records = ParserRegistry.bind_sites([target]).parse(source(*HISTORY), target)
    assert select_record(records, 230, target).zodiac == "龙兔"


@pytest.mark.parametrize("period", [227, 231])
def test_rejects_period_outside_bottom_three(period: int) -> None:
    records = parse_mengxiang_rensheng_manager_article_records(source(*HISTORY), site())

    with pytest.raises(ValueError, match=rf"bottom 候选内未找到 {period} 期"):
        select_record(records, period, site())


def test_rejects_wrong_identity_direction_payload_and_field() -> None:
    valid = source(*HISTORY)
    wrong_id = URL.replace("6a55eceff447e21b02daa681", "6a33d2d2dfa16552b923d0af")

    assert parse_mengxiang_rensheng_manager_article_records(valid, site(name="霸王传奇")) == []
    assert parse_mengxiang_rensheng_manager_article_records(valid, site(pick="top")) == []
    assert parse_mengxiang_rensheng_manager_article_records(valid, site(url=wrong_id)) == []
    assert parse_mengxiang_rensheng_manager_article_records(valid, site(payload="page")) == []
    assert parse_mengxiang_rensheng_manager_article_records(
        valid.replace("绝杀二肖", "稳杀三肖"), site()
    ) == []


def test_repeated_zodiac_never_forms_a_valid_230_result() -> None:
    records = parse_mengxiang_rensheng_manager_article_records(
        source(*HISTORY[:-1], (230, "龙龙", "000准")), site()
    )

    assert not any(
        record.period == 230 and validate_selected_record(record, 230)
        for record in records
    )


def test_same_period_different_values_is_a_conflict() -> None:
    records = parse_mengxiang_rensheng_manager_article_records(
        source(*HISTORY, (230, "狗猪", "000准")), site()
    )

    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 230, site())


def test_rejects_cross_document_anchor_borrowing() -> None:
    combined = (
        "<p>梦想人生</p>"
        + DOCUMENT_BOUNDARY
        + "<p>230期：《霸王传奇》绝杀二肖【龙兔】开：000准</p>"
    )

    assert parse_mengxiang_rensheng_manager_article_records(combined, site()) == []
