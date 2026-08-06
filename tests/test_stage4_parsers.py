from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from config.loader import load_sites
from domain.errors import ConfigurationError
from domain.models import PayloadDocument, Site
from parsers.registry import ENGINE_REGISTRY, ParserRegistry


ROOT = Path(__file__).resolve().parents[1]
SITES_PATH = ROOT / "config" / "sites.json"


def configured_parser_names() -> set[str]:
    data = json.loads(SITES_PATH.read_text(encoding="utf-8"))
    return {str(item.get("parser") or "named_block") for item in data}


def configured_sites() -> list[Site]:
    return load_sites(SITES_PATH, allowed_parsers=ENGINE_REGISTRY)


def site_for(name: str) -> Site:
    return next(site for site in configured_sites() if site.name == name)


def test_all_215_active_sites_have_one_explicit_identity_binding() -> None:
    sites = configured_sites()
    registry = ParserRegistry.bind_sites(sites)

    assert len(sites) == 215
    assert set(configured_parser_names()) <= set(ENGINE_REGISTRY)
    assert len(registry.bindings) == 215
    assert set(registry.bindings) == {site.identity for site in sites}
    assert all(registry.bindings[site.identity] == site.parser for site in sites)


def test_unknown_parser_and_unbound_identity_are_rejected() -> None:
    sites = configured_sites()
    registry = ParserRegistry.bind_sites(sites)
    unbound = Site("未入库", "top", "https://example.test/topic/1", parser="named_block")

    with pytest.raises(ConfigurationError, match="未绑定"):
        registry.parse("209期【狗蛇】开:00", unbound)

    bad = Site("坏配置", "top", "https://example.test/topic/2", parser="does_not_exist")
    with pytest.raises(ConfigurationError, match="未知 parser"):
        ParserRegistry.bind_sites([bad])


def test_bound_parser_cannot_be_changed_after_startup() -> None:
    original = site_for("科方东文")
    registry = ParserRegistry.bind_sites([original])
    changed = Site(
        original.name,
        original.pick,
        original.url,
        parser="site_scoped_two_zodiac" if original.parser == "named_block" else "named_block",
        title=original.title,
        record=original.record,
        stop=original.stop,
        payload=original.payload,
    )

    with pytest.raises(ConfigurationError, match="绑定不一致"):
        registry.parse("", changed)


def test_named_block_keeps_site_anchor_and_rejects_repeated_zodiac() -> None:
    site = Site(
        "测试目录",
        "top",
        "https://example.test/topic/1",
        parser="named_block",
        title=r"测试目录",
        record=r"(?P<period>\d{3})期【(?P<zodiac>[牛马羊鸡狗猪鼠虎兔龙蛇猴]{2})】开:(?P<open>\S+)",
        payload="page",
    )
    registry = ParserRegistry.bind_sites([site])
    source = "诱饵目录 209期【龙虎】开:00 测试目录 209期【狗蛇】开:11 208期【牛牛】开:22"

    records = registry.parse(source, site)

    assert [(record.period, record.zodiac, record.position) for record in records] == [
        (209, "狗蛇", source.index("209期【狗蛇】"))
    ]


def test_site_scoped_parser_does_not_cross_into_adjacent_heading() -> None:
    site = Site(
        "目标作者",
        "top",
        "https://example.test/topic/1",
        parser="site_scoped_two_zodiac",
        title=r"目标作者",
        payload="page",
    )
    registry = ParserRegistry.bind_sites([site])
    source = """
        <h2>目标作者</h2>
        <p>209期：绝杀二肖【狗蛇】开：11</p>
        <h2>相邻作者</h2>
        <p>209期：绝杀二肖【龙虎】开：22</p>
    """

    records = registry.parse(source, site)

    assert [(record.period, record.zodiac) for record in records] == [(209, "狗蛇")]


def test_manager_parser_requires_its_own_name_and_keeps_document_provenance() -> None:
    site = Site(
        "彻头彻尾",
        "bottom",
        "https://example.test/article/manager/target-id?url=jbx",
        parser="manager_article_two_zodiac",
        payload="admin_article_api",
    )
    registry = ParserRegistry.bind_sites([site])
    document = PayloadDocument(
        "后台文章",
        "https://example.test/api/target-id",
        "209期：《诱饵作者》绝杀二肖【龙虎】开:00 209期：《彻头彻尾》绝杀二肖【狗蛇】开:11",
        record_id="target-id",
        record_path="root.items[4]",
        record_count=8,
    )

    records = registry.parse(document, site)

    assert [(record.period, record.zodiac) for record in records] == [(209, "狗蛇")]
    assert records[0].record_id == "target-id"
    assert records[0].document_url == document.url


def test_ten_zodiac_complement_requires_exactly_ten_unique_items() -> None:
    site = Site(
        "老大主",
        "bottom",
        "https://example.test/",
        parser="laodazhu_ten_zodiac_complement",
        payload="page",
    )
    registry = ParserRegistry.bind_sites([site])
    source = "【老大主十肖】209期【牛马羊鸡狗猪鼠虎兔龙】208期【牛马羊鸡狗猪鼠虎兔兔】【老二主四头】"

    records = registry.parse(source, site)

    assert [(record.period, record.zodiac) for record in records] == [(209, "蛇猴")]


@pytest.mark.parametrize("pick", ["top", "bottom"])
def test_user_forum_parser_raises_structured_same_snapshot_conflict_before_direction(pick: str) -> None:
    site = Site(
        "繁忙棒球",
        pick,
        "https://example.test/#/users/3978",
        parser="tuku_user_forums_two_zodiac",
        payload="tuku_user_forums",
    )
    registry = ParserRegistry.bind_sites([site])
    source = json.dumps(
        [
            {"draw": 209, "topic": "绝杀二肖", "content": "209期绝杀二肖【狗蛇】开:11"},
            {"draw": 209, "topic": "绝杀二肖", "content": "209期绝杀二肖【龙虎】开:22"},
        ],
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="数据存在冲突.*狗蛇.*龙虎"):
        registry.parse(source, site)


def test_user_forum_parser_does_not_apply_top_or_bottom_window() -> None:
    source = (ROOT / "parsers" / "chart.py").read_text(encoding="utf-8")
    function = source.split("def records_from_forum_snapshots", 1)[1].split(
        "def parse_tuku_user_forums_two_zodiac_records",
        1,
    )[0]

    assert "site.pick" not in function
    assert "[:3]" not in function and "[-3:]" not in function


def test_yichou_parser_preserves_same_period_conflict_for_validation_stage() -> None:
    site = Site(
        "一筹莫展",
        "top",
        "https://example.test/topic/1",
        parser="yichou_mozhan_top",
        payload="page",
    )
    registry = ParserRegistry.bind_sites([site])
    source = """
        209期：一筹莫展【绝杀二肖】 一筹莫展 发表于
        209期：绝杀二肖【狗蛇】开：11
        209期：绝杀二肖【龙虎】开：22
        下一篇：其他
    """

    records = registry.parse(source, site)

    assert [(record.period, record.zodiac) for record in records] == [(209, "狗蛇"), (209, "龙虎")]


def test_four_digit_period_tokens_are_masked_before_parsing() -> None:
    site = Site(
        "测试目录",
        "top",
        "https://example.test/topic/1",
        parser="named_block",
        title=r"测试目录",
        record=r"(?P<period>\d{3})期【(?P<zodiac>[牛马羊鸡狗猪鼠虎兔龙蛇猴]{2})】开:(?P<open>\S+)",
        payload="page",
    )
    registry = ParserRegistry.bind_sites([site])

    assert registry.parse("测试目录 1209期【狗蛇】开:11", site) == []


def test_parser_layer_has_no_forbidden_dependencies() -> None:
    forbidden = {"fetching", "validation", "cache", "services", "output"}
    violations: list[str] = []
    for path in (ROOT / "parsers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                continue
            for root in roots & forbidden:
                violations.append(f"{path.name}:{node.lineno}:{root}")
    assert violations == []
