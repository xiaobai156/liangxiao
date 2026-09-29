from __future__ import annotations

from parsers.helpers import records_from_two_zodiac_text


PAGE = "267期: 🌿瞬杀二肖🌿 开: 兔40 对\n【 狗 牛 】 272期: 🌿瞬杀二肖🌿 开: 00 对\n【鼠 猴】"


def test_shunsha_two_zodiac_rows_parse() -> None:
    records = records_from_two_zodiac_text(PAGE)

    assert [(record.period, record.zodiac) for record in records] == [(267, "狗牛"), (272, "鼠猴")]


def test_jiangsha_two_zodiac_label_still_parses() -> None:
    records = records_from_two_zodiac_text("271期: 将杀两肖 开: 猴35 对\n【 兔 羊 】")

    assert [(record.period, record.zodiac) for record in records] == [(271, "兔羊")]


def test_neighbour_column_label_is_not_swallowed() -> None:
    assert records_from_two_zodiac_text("272期: 🌿瞬杀一肖🌿 开: 00 对\n【鼠 猴】") == []
