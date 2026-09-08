from __future__ import annotations

import pytest

from domain.models import DocumentBundle, PayloadDocument, Record, Site
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period


SITE_URL = "https://vds8y.2wu98-q1kie-wjsdjo.xyz/topic/384835.html"
SCRIPT_URL = "https://vds8y.2wu98-q1kie-wjsdjo.xyz/topic/384835.js"
SCRIPT_REFERENCE = "/topic/384835.js"
RECORD_ID = "topic:384835"
TITLE = r"〖\s*绝杀二肖\s*〗"
RECORD = (
    r"(?P<period>\d{3})\s*期[^期]{0,90}?"
    r"(?:绝\s*杀|稳\s*杀|精\s*杀|无错\s*杀|砍\s*杀|杀)\s*"
    r"(?:二|两|2|２|②)\s*肖[^期]{0,50}?"
    r"[【〖\[（(《『〈]\s*"
    r"(?P<zodiac>[牛马羊鸡狗猪鼠虎兔龙蛇猴]\s*"
    r"[-－、,，.。· ]?\s*[牛马羊鸡狗猪鼠虎兔龙蛇猴])\s*"
    r"[】〗\]）)》』〉]\s*(?:←|[~～_\-－—|]*)\s*"
    r"(?:开|開)\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
)

TOP_ROWS = ((236, "鸡虎"), (235, "牛鼠"), (234, "猪马"), (233, "虎兔"))
BOTTOM_ROWS = ((233, "虎兔"), (234, "猪马"), (235, "牛鼠"), (236, "鸡虎"))


def target_site(pick: str) -> Site:
    return Site(
        "呻吟成瘾",
        pick,
        SITE_URL,
        title=TITLE,
        record=RECORD,
        payload="page_and_scripts",
    )


def record_rows(rows: tuple[tuple[int, str], ...]) -> str:
    return "".join(
        f"<p>{period:03d}期绝杀二肖【{zodiac}】开:00准</p>"
        for period, zodiac in rows
    )


def bundle_with_script(
    rows: tuple[tuple[int, str], ...],
    *,
    page_rows: tuple[tuple[int, str], ...] = (),
    page_references_script: bool = True,
    script_url: str = SCRIPT_URL,
    script_record_id: str = RECORD_ID,
    script_parent_url: str = SITE_URL,
    script_link_reference: str = SCRIPT_REFERENCE,
    script_has_title: bool = False,
) -> DocumentBundle:
    reference = (
        f'<script src="{SCRIPT_REFERENCE}"></script>'
        if page_references_script
        else ""
    )
    page_source = (
        "<h2>呻吟成瘾</h2>"
        "<div>〖绝杀二肖〗</div>"
        f"{record_rows(page_rows)}"
        f"{reference}"
    )
    script_source = (
        ("<div>〖绝杀二肖〗</div>" if script_has_title else "")
        + record_rows(rows)
    )
    return DocumentBundle(
        (
            PayloadDocument("页面", SITE_URL, page_source, record_id=RECORD_ID),
            PayloadDocument(
                "脚本",
                script_url,
                script_source,
                record_id=script_record_id,
                parent_url=script_parent_url,
                link_reference=script_link_reference,
            ),
        )
    )


def parse_bundle(
    pick: str,
    period: int,
    bundle: DocumentBundle,
) -> tuple[list[Record], Record]:
    site = target_site(pick)
    return parse_bundle_for_period(bundle, site, period, ParserRegistry.bind_sites([site]))


@pytest.mark.parametrize(
    ("pick", "rows"),
    (("top", TOP_ROWS), ("bottom", BOTTOM_ROWS)),
    ids=("top-window", "bottom-window"),
)
def test_referenced_script_can_supply_complete_236_record(
    pick: str,
    rows: tuple[tuple[int, str], ...],
) -> None:
    records, selected = parse_bundle(pick, 236, bundle_with_script(rows))

    assert [record.period for record in records] == [period for period, _ in rows]
    assert selected.period == 236
    assert selected.zodiac == "鸡虎"


@pytest.mark.parametrize(
    ("pick", "rows", "outside_period"),
    (
        ("top", TOP_ROWS, 233),
        ("bottom", BOTTOM_ROWS, 233),
    ),
    ids=("top-window-boundary", "bottom-window-boundary"),
)
def test_referenced_script_keeps_three_record_direction_window(
    pick: str,
    rows: tuple[tuple[int, str], ...],
    outside_period: int,
) -> None:
    with pytest.raises(ValueError, match=rf"{pick} 候选内未找到 {outside_period} 期"):
        parse_bundle(pick, outside_period, bundle_with_script(rows))


def test_script_without_html_reference_cannot_supply_236() -> None:
    with pytest.raises(ValueError, match="父文档未引用"):
        parse_bundle(
            "bottom",
            236,
            bundle_with_script(BOTTOM_ROWS, page_references_script=False),
        )


def test_referenced_script_with_cross_record_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="边界冲突"):
        parse_bundle(
            "bottom",
            236,
            bundle_with_script(BOTTOM_ROWS, script_record_id="topic:other"),
        )


def test_different_url_without_reference_evidence_cannot_supply_236() -> None:
    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle(
            "bottom",
            236,
            bundle_with_script(
                BOTTOM_ROWS,
                page_references_script=False,
                script_url="https://unrelated.example/236.js",
                script_parent_url="",
                script_link_reference="",
            ),
        )


@pytest.mark.parametrize(
    ("pick", "page_rows"),
    (
        ("top", ((236, "鸡虎"), (235, "牛鼠"), (234, "猪马"))),
        ("bottom", ((234, "猪马"), (235, "牛鼠"), (236, "鸡虎"))),
    ),
    ids=("top-conflict", "bottom-conflict"),
)
def test_same_period_conflict_between_page_and_script_still_fails(
    pick: str,
    page_rows: tuple[tuple[int, str], ...],
) -> None:
    with pytest.raises(ValueError, match="数据存在冲突"):
        parse_bundle(
            pick,
            236,
            bundle_with_script(
                ((236, "马猴"),),
                page_rows=page_rows,
                script_has_title=True,
            ),
        )
