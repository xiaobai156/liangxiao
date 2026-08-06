from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument, Site
from parsers.dynamic import parse_xuanji_tianshu_admin_article_records
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[1]
ARTICLE_ID = "6a33d28bdfa16552b923d0a5"
ARTICLE_URL = (
    "https://mbsqhpk.8ivvt-u3cx5-enwlld.xyz:29400/"
    f"article/manager/{ARTICLE_ID}?url=lhzj"
)
API_URL = (
    "https://mbsqhpk.8ivvt-u3cx5-enwlld.xyz:29400/"
    "api/proxy/landing-page-data?url=lhzj"
)


def candidate_site() -> Site:
    return Site(
        "玄机天书",
        "bottom",
        ARTICLE_URL,
        parser="xuanji_tianshu_admin_article",
        payload="admin_article_api",
        api_url=API_URL,
    )


def target_source() -> str:
    return (
        "213期: 🧤 『玄机天书』 🧤 绝杀二肖 🧤【蛇.狗】开:猴35准 "
        "214期: 🧤 『玄机天书』 🧤 绝杀二肖 🧤【虎.猴】开:兔04准 "
        "215期: 🧤 『玄机天书』 🧤 绝杀二肖 🧤【羊.蛇】开:蛇14错 "
        "216期: 🧤 『玄机天书』 🧤 绝杀二肖 🧤【龙.马】开:00准"
    )


def target_document(source: str | None = None, *, record_id: str = ARTICLE_ID) -> PayloadDocument:
    return PayloadDocument(
        "后台文章",
        API_URL,
        source or target_source(),
        record_id=record_id,
        record_path="root.sectionData.section.adminArticles[7]",
        record_count=108,
    )


def test_xuanji_tianshu_is_explicitly_bound_to_its_manager_article() -> None:
    configured = next(
        site
        for site in load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
        if site.name == "玄机天书"
    )

    assert (configured.pick, configured.parser, configured.payload) == (
        "bottom",
        "xuanji_tianshu_admin_article",
        "admin_article_api",
    )
    assert configured.url == ARTICLE_URL
    assert configured.api_url == API_URL


def test_xuanji_tianshu_bottom_window_and_article_provenance() -> None:
    site = candidate_site()
    registry = ParserRegistry.bind_sites([site])
    document = target_document()
    records = registry.parse(document, site)

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (214, "虎猴"),
        (215, "羊蛇"),
        (216, "龙马"),
    ]
    selected = select_record(records, 216, site)
    assert (selected.zodiac, selected.record_id, selected.record_path) == (
        "龙马",
        ARTICLE_ID,
        document.record_path,
    )
    with pytest.raises(ValueError, match="bottom 候选内未找到 213 期"):
        select_record(records, 213, site)
    with pytest.raises(ValueError, match="bottom 候选内未找到 217 期"):
        select_record(records, 217, site)


@pytest.mark.parametrize(
    "source",
    (
        target_source().replace("玄机天书", "其他作者"),
        target_source().replace("🧤", "🧶"),
        target_source().replace("绝杀二肖", "绝杀三肖"),
    ),
)
def test_xuanji_tianshu_rejects_wrong_identity_marker_semantic_or_value(source: str) -> None:
    assert parse_xuanji_tianshu_admin_article_records(source, candidate_site()) == []


def test_xuanji_tianshu_repeated_zodiac_does_not_enter_direction_window() -> None:
    site = candidate_site()
    records = parse_xuanji_tianshu_admin_article_records(
        target_source().replace("【龙.马】", "【龙.龙】"),
        site,
    )

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (213, "蛇狗"),
        (214, "虎猴"),
        (215, "羊蛇"),
    ]
    with pytest.raises(ValueError, match="bottom 候选内未找到 216 期"):
        select_record(records, 216, site)


def test_xuanji_tianshu_rejects_same_period_conflict_and_wrong_article_id() -> None:
    site = candidate_site()
    registry = ParserRegistry.bind_sites([site])
    conflict = target_source() + " 216期: 🧤 『玄机天书』 🧤 绝杀二肖 🧤【牛.鼠】开:00准"

    with pytest.raises(ValueError, match="数据存在冲突.*龙马.*牛鼠"):
        select_record(registry.parse(conflict, site), 216, site)

    with pytest.raises(ValueError, match="文章ID边界冲突"):
        parse_bundle_for_period(
            DocumentBundle((target_document(record_id="wrong-article-id"),)),
            site,
            216,
            registry,
        )
