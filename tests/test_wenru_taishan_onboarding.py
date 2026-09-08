from __future__ import annotations

import pytest

from domain.models import DOCUMENT_BOUNDARY, Site
from parsers.forum import parse_wenru_taishan_top_records
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from validation.direction import select_record
from validation.records import validate_selected_record


def site(*, name: str = "稳如泰山", pick: str = "top") -> Site:
    return Site(
        name,
        pick,
        "https://azjjosd.scyms-vek4g-yubibb.xyz:17455/topic/253481.html",
        parser="wenru_taishan_top",
        title="稳如泰山",
        payload="browser_rendered_page",
    )


def source(*rows: tuple[int, str, str]) -> str:
    body = "".join(
        f"<p>{period:03d}期 稳如泰山【稳杀二肖】"
        f"<span>【{zodiac}】开</span>{open_result}</p>"
        for period, zodiac, open_result in rows
    )
    return (
        "<html><body><p>227期：稳如泰山【稳杀二肖】已公开</p>"
        f"{body}<p>站长宣言:</p></body></html>"
    )


HISTORY = (
    (227, "猪狗", "000准"),
    (226, "虎猪", "虎17错"),
    (225, "蛇马", "马01错"),
    (224, "蛇猴", "狗09准"),
    (223, "马蛇", "猴23准"),
    (222, "鼠马", "蛇26准"),
    (221, "马鼠", "马01错"),
    (220, "鼠马", "羊48准"),
    (219, "马虎", "鼠43准"),
    (218, "猴马", "虎17准"),
)


def test_parses_real_history_in_visible_order_and_selects_227_from_top_window() -> None:
    records = parse_wenru_taishan_top_records(source(*HISTORY), site())

    assert [(record.period, record.zodiac) for record in records] == [
        (period, zodiac) for period, zodiac, _open_result in HISTORY
    ]
    assert all(records[index].position < records[index + 1].position for index in range(9))
    selected = select_record(records, 227, site())
    assert selected.zodiac == "猪狗"
    assert validate_selected_record(selected, 227)


def test_parser_is_registered_and_bound_to_the_exact_site_identity() -> None:
    target = site()

    assert ENGINE_REGISTRY["wenru_taishan_top"] is parse_wenru_taishan_top_records
    registry = ParserRegistry.bind_sites([target])
    records = registry.parse(source(*HISTORY), target)
    assert select_record(records, 227, target).zodiac == "猪狗"


@pytest.mark.parametrize("period", [224, 228])
def test_rejects_period_outside_top_three(period: int) -> None:
    records = parse_wenru_taishan_top_records(source(*HISTORY), site())

    with pytest.raises(ValueError, match=rf"top 候选内未找到 {period} 期"):
        select_record(records, period, site())


def test_rejects_wrong_name_direction_and_field() -> None:
    valid_source = source(*HISTORY)
    assert parse_wenru_taishan_top_records(valid_source, site(name="别的站")) == []
    assert parse_wenru_taishan_top_records(valid_source, site(pick="bottom")) == []
    assert parse_wenru_taishan_top_records(
        valid_source.replace("稳杀二肖", "稳杀三肖"), site()
    ) == []


def test_repeated_zodiac_is_not_a_valid_selected_result() -> None:
    records = parse_wenru_taishan_top_records(
        source((227, "猪猪", "000准"), *HISTORY[1:]), site()
    )

    assert not any(
        record.period == 227 and validate_selected_record(record, 227)
        for record in records
    )


def test_same_period_different_values_is_a_conflict() -> None:
    records = parse_wenru_taishan_top_records(
        source((227, "猪狗", "000准"), (227, "虎猪", "000准"), *HISTORY[1:]),
        site(),
    )

    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 227, site())


def test_does_not_borrow_anchor_across_documents_or_parse_advertising() -> None:
    anchor_document = "<p>227期：稳如泰山【稳杀二肖】已公开</p>"
    unrelated_document = (
        "<p>227期 别的站【稳杀二肖】【猪狗】开000准</p>"
        "<p>广告：稳如泰山 博彩必备 猪狗</p>"
    )

    assert parse_wenru_taishan_top_records(
        anchor_document + DOCUMENT_BOUNDARY + unrelated_document, site()
    ) == []
