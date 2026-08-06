from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import PayloadDocument, Site
from parsers.dynamic import (
    parse_jinbao_mawang_manager_article_records,
    parse_rushen_tantao_read_page_records,
)
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[1]


def configured_site(name: str) -> Site:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    return next(site for site in sites if site.name == name)


def test_special_case_sites_are_explicitly_bound_to_bottom_sources() -> None:
    jinbao = configured_site("金宝码王")
    rushen = configured_site("如神探讨")

    assert (jinbao.pick, jinbao.parser, jinbao.payload) == (
        "bottom",
        "jinbao_mawang_manager_article",
        "admin_article_api",
    )
    assert jinbao.api_url.endswith("/api/proxy/landing-page-data?url=tdg")
    assert (rushen.pick, rushen.parser, rushen.payload) == (
        "bottom",
        "rushen_tantao_read_page",
        "page",
    )


def test_jinbao_manager_parser_keeps_article_identity_and_bottom_window() -> None:
    site = Site(
        "金宝码王",
        "bottom",
        "https://example.test/article/manager/article-id?url=tdg",
        parser="jinbao_mawang_manager_article",
        payload="admin_article_api",
    )
    source = (
        "212期: 〖金宝码王〗 🧶 绝杀二肖 🧶【龙.牛】开:牛06错 "
        "213期: 〖金宝码王〗 🧶 绝杀二肖 🧶【马.兔】开:猴35准 "
        "214期: 〖金宝码王〗 🧶 绝杀二肖 🧶【鼠.猪】开:兔04准 "
        "215期: 〖金宝码王〗 🧶 绝杀二肖 🧶【猴.蛇】开:00准"
    )
    document = PayloadDocument(
        "后台文章",
        "https://example.test/api/proxy/landing-page-data?url=tdg",
        source,
        record_id="article-id",
        record_path="root.section.adminArticles[0]",
        record_count=90,
    )
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(document, site)

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (213, "马兔"),
        (214, "鼠猪"),
        (215, "猴蛇"),
    ]
    selected = select_record(records, 215, site)
    assert selected.record_id == "article-id"
    assert selected.record_path == "root.section.adminArticles[0]"
    with pytest.raises(ValueError, match="bottom 候选内未找到 212 期"):
        select_record(records, 212, site)


def test_rushen_read_parser_uses_exact_author_and_bottom_window() -> None:
    site = Site(
        "如神探讨",
        "bottom",
        "https://example.test/read.php?tid=751",
        parser="rushen_tantao_read_page",
        payload="page",
    )
    source = (
        "212期: 【如神探讨】 🚲 绝杀②肖 🚲【虎.牛】开:牛06错 "
        "213期: 【如神探讨】 🚲 绝杀②肖 🚲【蛇.羊】开:猴35准 "
        "214期: 【如神探讨】 🚲 绝杀②肖 🚲【狗.羊】开:兔04准 "
        "215期: 【如神探讨】 🚲 绝杀②肖 🚲【狗.牛】开:00准"
    )
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source, site)

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (213, "蛇羊"),
        (214, "狗羊"),
        (215, "狗牛"),
    ]
    assert select_record(records, 215, site).zodiac == "狗牛"
    with pytest.raises(ValueError, match="bottom 候选内未找到 216 期"):
        select_record(records, 216, site)


@pytest.mark.parametrize(
    ("parser", "site", "source"),
    (
        (
            parse_jinbao_mawang_manager_article_records,
            Site("其他作者", "bottom", "https://example.test/"),
            "215期: 〖金宝码王〗 🧶 绝杀二肖 🧶【猴.蛇】开:00准",
        ),
        (
            parse_rushen_tantao_read_page_records,
            Site("其他作者", "bottom", "https://example.test/"),
            "215期: 【如神探讨】 🚲 绝杀②肖 🚲【狗.牛】开:00准",
        ),
        (
            parse_jinbao_mawang_manager_article_records,
            Site("金宝码王", "bottom", "https://example.test/"),
            "215期: 〖金宝码王〗 🧶 必杀二肖 🧶【猴.蛇】开:00准",
        ),
        (
            parse_rushen_tantao_read_page_records,
            Site("如神探讨", "bottom", "https://example.test/"),
            "215期: 【如神探讨】 🚲 绝杀三肖 🚲【狗.牛】开:00准",
        ),
        (
            parse_jinbao_mawang_manager_article_records,
            Site("金宝码王", "bottom", "https://example.test/"),
            "215期: 〖金宝码王〗 🧶 绝杀二肖 🧶【猴.猴】开:00准",
        ),
    ),
)
def test_special_case_parsers_reject_wrong_identity_semantic_or_repeated_zodiac(
    parser, site, source
) -> None:
    assert parser(source, site) == []


@pytest.mark.parametrize(
    ("parser_name", "site_name", "record"),
    (
        ("jinbao_mawang_manager_article", "金宝码王", "〖金宝码王〗 🧶 绝杀二肖 🧶"),
        ("rushen_tantao_read_page", "如神探讨", "【如神探讨】 🚲 绝杀②肖 🚲"),
    ),
)
def test_special_case_parsers_preserve_same_period_conflicts(
    parser_name: str, site_name: str, record: str
) -> None:
    site = Site(site_name, "bottom", "https://example.test/", parser=parser_name)
    registry = ParserRegistry.bind_sites([site])
    source = (
        f"213期: {record}【蛇.羊】开:猴35准 "
        f"214期: {record}【狗.羊】开:兔04准 "
        f"215期: {record}【狗.牛】开:00准 "
        f"215期: {record}【猴.蛇】开:00准"
    )

    records = registry.parse(source, site)

    with pytest.raises(ValueError, match="数据存在冲突.*狗牛.*猴蛇"):
        select_record(records, 215, site)
