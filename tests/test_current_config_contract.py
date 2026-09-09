from __future__ import annotations

import json
import re
from pathlib import Path

from cache.contracts import config_fingerprint, validate_cache_position_contract
from cache.repository import RecentCacheRepository
from config.loader import load_sites
from domain.models import Result
from parsers.registry import ENGINE_REGISTRY


ROOT = Path(__file__).resolve().parents[1]


def _formal_sites():
    return load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)


def _site_by_name(name: str):
    return next(site for site in _formal_sites() if site.name == name)


def test_formal_cache_matches_current_configuration_contract() -> None:
    sites = _formal_sites()
    cache = json.loads((ROOT / "recent_10_cache.json").read_text(encoding="utf-8-sig"))

    validate_cache_position_contract(cache)
    assert cache["config_fingerprint"] == config_fingerprint(sites)
    assert [
        (entry["name"], entry["url"], entry["pick"])
        for entry in cache["sites"]
    ] == [site.identity for site in sites]

    prepared = RecentCacheRepository(ROOT / "recent_10_cache.json").prepare_update(
        [Result(site=site, error="离线契约测试") for site in sites],
        cache["issues"][0],
    )
    assert prepared is not None
    assert prepared["config_fingerprint"] == cache["config_fingerprint"]


def test_current_named_block_patterns_accept_pig_in_either_position() -> None:
    cases = {
        "斗转星移": (
            "251期斗转星移：【猪牛】开:00",
            "251期斗转星移：【牛猪】开:00",
        ),
        "东方心经": (
            "251期东方心经无错杀肖：【猪牛】开:00",
            "251期东方心经无错杀肖：【牛猪】开:00",
        ),
    }

    for name, samples in cases.items():
        pattern = re.compile(_site_by_name(name).record)
        assert all(pattern.search(sample) is not None for sample in samples)


def test_dongfang_xinjing_stop_boundary_is_not_tied_to_period_225() -> None:
    site = _site_by_name("东方心经")

    assert "225" not in site.stop
    assert re.search(site.stop, "上一篇：251期：东方心经") is not None
