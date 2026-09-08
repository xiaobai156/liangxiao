from __future__ import annotations

import base64
import json

import pytest

from domain.models import DOCUMENT_BOUNDARY, Site
from fetching.dynamic_article import decode_admin_article_api_response
from parsers.manager_234 import (
    parse_jinma_dushen_manager_article_records,
    parse_yanji_dushan_manager_article_records,
    parse_zhuangba_disheng_manager_article_records,
)
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.direction import select_record
from validation.records import validate_selected_record


CASES = (
    (
        "妆罢低声",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a4e87bd57dc857ae1c13558?url=dyj",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/api/proxy/landing-page-data?url=dyj",
        "zhuangba_disheng_manager_article",
        parse_zhuangba_disheng_manager_article_records,
        "《妆罢低声》 👈 绝杀二肖 👈",
        ((231, "牛蛇"), (232, "狗鸡"), (233, "兔虎"), (234, "马鼠")),
    ),
    (
        "燕姬独擅",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a64ec62b3f65fed7d6286e7?url=dyj",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/api/proxy/landing-page-data?url=dyj",
        "yanji_dushan_manager_article",
        parse_yanji_dushan_manager_article_records,
        "《燕姬独擅》 🎆 绝杀二肖 🎆",
        ((231, "牛蛇"), (232, "鸡猴"), (233, "兔虎"), (234, "马鼠")),
    ),
    (
        "金码赌神",
        "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/article/manager/6a081b9be0d076537e1df8a6?url=jdb",
        "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/api/proxy/landing-page-data?url=jdb",
        "jinma_dushen_manager_article",
        parse_jinma_dushen_manager_article_records,
        "〖金码赌神〗 🥽 绝杀②肖 🥽",
        ((231, "马牛"), (232, "牛蛇"), (233, "蛇羊"), (234, "鼠猪")),
    ),
)


def make_site(case: tuple[object, ...], **changes: str) -> Site:
    name, url, api_url, parser_id, _parser, _marker, _history = case
    values = {
        "name": str(name),
        "pick": "bottom",
        "url": str(url),
        "parser": str(parser_id),
        "payload": "admin_article_api",
        "api_url": str(api_url),
    }
    values.update(changes)
    return Site(**values)


def make_source(case: tuple[object, ...], rows: tuple[tuple[int, str], ...] | None = None) -> str:
    marker = str(case[5])
    values = rows or case[6]
    return "".join(
        f"<p>{period:03d}期: {marker}【{zodiac[0]}.{zodiac[1]}】开: 0000</p>"
        for period, zodiac in values
    )


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_exact_parser_is_registered_and_selects_234(case: tuple[object, ...]) -> None:
    site = make_site(case)
    parser = case[4]
    parser_id = str(case[3])

    assert ENGINE_REGISTRY[parser_id] is parser
    records = ParserRegistry.bind_sites([site]).parse(make_source(case), site)
    selected = select_record(records, 234, site)
    assert selected.zodiac == case[6][-1][1]
    assert validate_selected_record(selected, 234)


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_bottom_window_rejects_231(case: tuple[object, ...]) -> None:
    site = make_site(case)
    records = case[4](make_source(case), site)

    with pytest.raises(ValueError, match="bottom 候选内未找到 231 期"):
        select_record(records, 231, site)


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_parser_rejects_wrong_identity_direction_payload_and_field(case: tuple[object, ...]) -> None:
    parser = case[4]
    source = make_source(case)
    wrong_id = str(case[1]).replace(str(case[1]).split("/article/manager/")[1].split("?")[0], "wrong-id")

    assert parser(source, make_site(case, name="错误名称")) == []
    assert parser(source, make_site(case, pick="top")) == []
    assert parser(source, make_site(case, url=wrong_id)) == []
    assert parser(source, make_site(case, payload="page")) == []
    assert parser(source.replace("绝杀二肖", "绝杀三肖").replace("绝杀②肖", "绝杀三肖"), make_site(case)) == []


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_public_wrapper_keeps_own_identity_when_site_parser_is_empty(case: tuple[object, ...]) -> None:
    parser = case[4]
    target = make_site(case, parser="")

    records = parser(make_source(case), target)

    assert select_record(records, 234, target).zodiac == case[6][-1][1]


@pytest.mark.parametrize(
    ("wrapper_case", "foreign_case"),
    ((CASES[0], CASES[1]), (CASES[1], CASES[2]), (CASES[2], CASES[0])),
    ids=("zhuangba-vs-yanji", "yanji-vs-jinma", "jinma-vs-zhuangba"),
)
def test_public_wrapper_rejects_another_manager_identity(
    wrapper_case: tuple[object, ...], foreign_case: tuple[object, ...]
) -> None:
    wrapper = wrapper_case[4]

    assert wrapper(make_source(foreign_case), make_site(foreign_case)) == []


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_repeated_zodiac_and_cross_document_are_rejected(case: tuple[object, ...]) -> None:
    parser = case[4]
    repeated = make_source(case, ((232, "狗鸡"), (233, "兔虎"), (234, "鼠鼠")))

    assert not any(record.period == 234 for record in parser(repeated, make_site(case)))
    assert parser(make_source(case) + DOCUMENT_BOUNDARY + make_source(case), make_site(case)) == []


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_same_period_different_values_is_conflict(case: tuple[object, ...]) -> None:
    site = make_site(case)
    rows = tuple(case[6]) + ((234, "牛羊"),)
    records = case[4](make_source(case, rows), site)

    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 234, site)


@pytest.mark.parametrize("case", CASES, ids=[str(case[0]) for case in CASES])
def test_unique_api_article_id_reaches_parser(case: tuple[object, ...]) -> None:
    site = make_site(case)
    article_id = str(case[1]).split("/article/manager/")[1].split("?")[0]
    encoded_title = base64.b64encode(str(case[0]).encode()).decode()
    encoded_html = base64.b64encode(make_source(case).encode()).decode()
    api_json = json.dumps(
        {
            "items": [
                {"id": "unrelated", "title": encoded_title, "html": encoded_html},
                {"id": article_id, "title": encoded_title, "html": encoded_html},
            ]
        }
    )
    bundle = decode_admin_article_api_response(api_json, article_id, site.api_url)
    records, selected = parse_bundle_for_period(
        bundle,
        site,
        234,
        ParserRegistry.bind_sites([site]),
    )

    assert len(records) == 4
    assert selected.zodiac == case[6][-1][1]
