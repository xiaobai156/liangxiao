from __future__ import annotations

import json
import re
from pathlib import Path

from config.loader import load_sites
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from validation.boundaries import expected_record_id


ROOT = Path(__file__).resolve().parents[1]


def configured_sites():
    return load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)


def test_all_215_active_sites_have_unique_dedicated_binding_and_no_generic_parser() -> None:
    sites = configured_sites()
    registry = ParserRegistry.bind_sites(sites)

    assert len(sites) == 215
    assert len(registry.bindings) == 215
    assert all(site.parser != "generic_two_zodiac" for site in sites)
    assert sum(site.pick == "top" for site in sites) == 119
    assert sum(site.pick == "bottom" for site in sites) == 96


def test_archived_sites_are_preserved_but_excluded_from_active_configuration() -> None:
    archived = json.loads(
        (ROOT / "config" / "archived_sites.json").read_text(encoding="utf-8")
    )
    archived_names = {item["name"] for item in archived}
    active_names = {site.name for site in configured_sites()}

    assert archived_names == {"赌神没睡醒", "登堂入室", "德艺双馨", "全球预测"}
    assert archived_names.isdisjoint(active_names)
    assert all(item.get("archive_reason") for item in archived)


def test_shared_dedicated_engines_have_explicit_anchor_and_record_contract() -> None:
    sites = configured_sites()
    named = [site for site in sites if site.parser == "named_block"]
    scoped = [site for site in sites if site.parser == "site_scoped_two_zodiac"]

    assert named and scoped
    assert all(site.title and site.record for site in named)
    assert all(site.title for site in scoped)


def test_active_configuration_authorizes_only_explicit_cross_document_sources() -> None:
    sites = configured_sites()

    linked = [site for site in sites if site.linked_document_pattern]
    assert [site.name for site in linked] == ["六合头条", "武林高手", "广西仔", "广东"]
    assert all(site.payload == "page_and_scripts" for site in linked)
    assert all(site.parser.endswith("linked_top") for site in linked)


def test_dynamic_article_sites_have_exact_url_record_id_and_api_boundary() -> None:
    dynamic = [
        site
        for site in configured_sites()
        if "/article/admin/" in site.url.lower() or "/article/manager/" in site.url.lower()
    ]

    assert dynamic
    assert all(site.payload == "admin_article_api" for site in dynamic)
    assert all(site.api_url for site in dynamic)
    assert all(expected_record_id(site) for site in dynamic)


def test_parser_layer_contains_no_direction_window_or_period_run_pretrim() -> None:
    violations: list[str] = []
    direction_slice = re.compile(
        r"(?:site\.pick[\s\S]{0,100}(?:\[:3\]|\[-3:\])|(?:\[:3\]|\[-3:\])[\s\S]{0,100}site\.pick)"
    )
    for path in (ROOT / "parsers").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "first_directional_period_run" in source or direction_slice.search(source):
            violations.append(path.name)

    assert violations == []
