from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import expected_record_id
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[1]


def configured_site():
    return next(
        site
        for site in load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
        if site.name == "狀元紅"
    )


def target_source() -> str:
    return (
        "澳门状元红 狀元紅⊙『绝杀②肖』 "
        "214期: 绝杀②肖 ❇ 〖兔猴〗开:？00 准 "
        "213期: 绝杀②肖 ❇ 〖鸡虎〗开:猴35 准 "
        "212期: 绝杀②肖 ❇ 〖牛猴〗开:牛06 准 "
        "211期: 绝杀②肖 ❇ 〖兔鼠〗开:马01 准 "
        "永久域名:01490150.com "
        "狀元紅01490150.com:你若想要富,用心来关注!"
    )


def test_zhuangyuan_red_has_exact_dedicated_contract() -> None:
    site = configured_site()

    assert site.pick == "top"
    assert site.parser == "zhuangyuan_red_top"
    assert site.title == r"澳门\s*状元红\s+狀元紅\s*⊙\s*[『〖【]\s*绝杀②肖\s*[』〗】]"


def test_zhuangyuan_red_uses_exact_section_and_top_window() -> None:
    site = configured_site()
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(target_source(), site)
    selected = parse_bundle_for_period(
        DocumentBundle(
            (
                PayloadDocument(
                    "原始页面",
                    site.url,
                    target_source(),
                    record_id=expected_record_id(site),
                ),
            )
        ),
        site,
        213,
        registry,
    )[1]

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (214, "兔猴"),
        (213, "鸡虎"),
        (212, "牛猴"),
    ]
    assert selected.zodiac == "鸡虎"
    with pytest.raises(ValueError, match="top 候选内未找到 211 期"):
        select_record(records, 211, site)


def test_zhuangyuan_red_rejects_duplicate_exact_sections_and_wrong_semantic() -> None:
    site = configured_site()
    registry = ParserRegistry.bind_sites([site])

    duplicate = target_source() + " " + target_source()
    wrong_semantic = target_source().replace("绝杀②肖", "绝杀二肖", 1)

    assert registry.parse(duplicate, site) == []
    assert registry.parse(wrong_semantic, site) == []
