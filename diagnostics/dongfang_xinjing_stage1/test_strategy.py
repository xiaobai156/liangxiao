from __future__ import annotations

import pytest

from domain.models import Site
from parsers.registry import ParserRegistry
from validation.direction import select_record
from validation.records import validate_selected_record


BROKEN_RECORD = (
    r"(?P<period>\d{3})\s*期\s*东方心经无错杀肖\s*[:：]?\s*"
    r"[【〖\[]\s*(?P<zodiac>[牛马羊鸡狗鼠虎兔龙蛇猴]\s*[-－、,，.。· ]?\s*"
    r"[牛马羊鸡狗猪鼠虎兔龙蛇猴])\s*[】〗\]]\s*[^0-9]{0,80}?"
    r"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
)


def site(*, fixed: bool) -> Site:
    record = BROKEN_RECORD
    if fixed:
        record = record.replace(
            "[牛马羊鸡狗鼠虎兔龙蛇猴]",
            "[牛马羊鸡狗猪鼠虎兔龙蛇猴]",
            1,
        )
    return Site(
        "东方心经",
        "top",
        "https://4.48kk49.com:1888/Article/ar_content/id/746/tid/52.html",
        parser="named_block",
        payload="page",
        title=r"东方心经[^\n]{0,80}无错杀肖",
        stop=r"上一篇\s*[：:]\s*225期\s*:\s*东方心经",
        record=record,
    )


def source(*rows: tuple[int, str]) -> str:
    body = "".join(
        f"<p>{period:03d}期 东方心经无错杀肖：【{zodiac}】开??准</p>"
        for period, zodiac in rows
    )
    return (
        "<h1>230期：东方心经☆无错杀肖已上料</h1>"
        f"{body}"
        "<p>上一篇：230期：东方心经☆准杀10码已上料</p>"
        "<p>下一篇：230期：东方心经☆绝杀一波已上料</p>"
    )


def parse(text: str, target: Site):
    return ParserRegistry.bind_sites([target]).parse(text, target)


def test_existing_rule_reproduces_the_230_pig_first_failure() -> None:
    assert parse(source((230, "猪羊")), site(fixed=False)) == []


def test_one_character_fix_selects_230_pig_sheep_from_top() -> None:
    target = site(fixed=True)
    selected = select_record(
        parse(source((230, "猪羊"), (229, "猴狗"), (228, "龙兔")), target),
        230,
        target,
    )

    assert selected.zodiac == "猪羊"
    assert validate_selected_record(selected, 230)


@pytest.mark.parametrize("period", [227, 231])
def test_rejects_period_outside_the_top_three(period: int) -> None:
    target = site(fixed=True)
    records = parse(source((230, "猪羊"), (229, "猴狗"), (228, "龙兔")), target)

    with pytest.raises(ValueError, match=rf"top 候选内未找到 {period} 期"):
        select_record(records, period, target)


def test_rejects_wrong_column_and_missing_anchor() -> None:
    target = site(fixed=True)
    wrong_column = source((230, "猪羊")).replace("无错杀肖", "准杀10码")
    missing_anchor = "<p>230期 别的栏目无错杀肖：【猪羊】开??准</p>"

    assert parse(wrong_column, target) == []
    assert parse(missing_anchor, target) == []


def test_repeated_zodiac_is_not_valid() -> None:
    target = site(fixed=True)
    records = parse(source((230, "猪猪")), target)

    assert not any(validate_selected_record(record, 230) for record in records)


def test_same_period_different_values_is_a_conflict() -> None:
    target = site(fixed=True)
    records = parse(source((230, "猪羊"), (230, "猪狗"), (229, "猴狗")), target)

    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 230, target)
