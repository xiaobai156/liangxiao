from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from cache.duplicates import (
    article_ids_from_entry,
    audit_cache_coverage,
    detect_duplicate_findings,
    positions_from_entry,
    values_from_entry,
)
from cache.guard import guard_results_against_cache
from cache.migration import migrate_legacy_cache
from cache.repository import RecentCacheRepository, recent_periods
from config.loader import load_sites
from domain.models import (
    POSITION_KIND_VISIBLE_TEXT,
    DocumentBundle,
    HistoryResult,
    PayloadDocument,
    Record,
    Result,
    Site,
)
from validation.boundaries import document_is_linked, expected_record_id, validate_bundle_boundaries
from validation.conflicts import validate_document_windows
from validation.direction import direction_window, select_record
from validation.records import validate_selected_record


ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(r"C:\Users\Administrator\Desktop\每天工具\数据系列\杀两肖-修复版")


def test_direction_window_and_manual_period_are_strict() -> None:
    top = Site("顶部站", "top", "https://example.test/top")
    bottom = Site("尾部站", "bottom", "https://example.test/bottom")
    records = [Record(period, "狗蛇", "", "", position) for position, period in enumerate((210, 209, 208, 207))]

    assert [record.period for record in direction_window(records, top)] == [210, 209, 208]
    assert [record.period for record in direction_window(records, bottom)] == [209, 208, 207]
    assert select_record(records, 208, top).period == 208
    with pytest.raises(ValueError, match="top 候选内未找到 207 期"):
        select_record(records, 207, top)


def test_selected_record_requires_exact_period_two_unique_zodiacs_and_position() -> None:
    assert validate_selected_record(Record(210, "狗蛇", "", "", 0), 210)
    assert not validate_selected_record(Record(209, "狗蛇", "", "", 0), 210)
    assert not validate_selected_record(Record(210, "狗狗", "", "", 0), 210)
    assert not validate_selected_record(Record(210, "狗A", "", "", 0), 210)
    assert not validate_selected_record(Record(210, "狗蛇", "", "", -1), 210)


def test_same_period_zodiac_or_position_conflict_is_rejected() -> None:
    site = Site("冲突站", "top", "https://example.test/topic/1")
    with pytest.raises(ValueError, match="209期出现多个候选"):
        select_record(
            [Record(209, "狗蛇", "", "", 0), Record(209, "龙虎", "", "", 0)],
            209,
            site,
        )
    validate_document_windows(
        [
            ("页面", [Record(209, "狗蛇", "", "", 0)]),
            ("脚本", [Record(209, "狗蛇", "", "", 1)]),
        ],
        site,
        "多文档",
    )
    with pytest.raises(ValueError, match="多文档近3条结果不同"):
        validate_document_windows(
            [
                ("页面", [Record(209, "狗蛇", "", "", 0)]),
                ("脚本", [Record(209, "龙虎", "", "", 1)]),
            ],
            site,
            "多文档",
        )


def test_dynamic_and_user_documents_require_exact_record_boundary() -> None:
    dynamic = Site(
        "动态站",
        "bottom",
        "https://example.test/article/manager/target-id?url=x",
        payload="admin_article_api",
    )
    valid = DocumentBundle((PayloadDocument("接口", "https://example.test/api", "正文", record_id="target-id"),))
    validate_bundle_boundaries(valid, dynamic)

    missing = DocumentBundle((PayloadDocument("接口", "https://example.test/api", "正文"),))
    with pytest.raises(ValueError, match="记录边界缺失"):
        validate_bundle_boundaries(missing, dynamic)

    wrong = DocumentBundle((PayloadDocument("接口", "https://example.test/api", "正文", record_id="decoy"),))
    with pytest.raises(ValueError, match="文章ID边界冲突"):
        validate_bundle_boundaries(wrong, dynamic)

    user = Site("用户站", "bottom", "https://example.test/#/users/3978", payload="tuku_user_forums")
    with pytest.raises(ValueError, match="用户ID边界冲突"):
        validate_bundle_boundaries(
            DocumentBundle((PayloadDocument("接口", "https://example.test/api", "正文", record_id="user:999"),)),
            user,
        )


def test_record_boundary_identity_and_explicit_document_links() -> None:
    assert expected_record_id(
        Site("动态", "top", "https://a.test/article/admin/abc?url=x", payload="admin_article_api")
    ) == "abc"
    assert expected_record_id(
        Site("用户", "top", "https://a.test/#/users/12", payload="tuku_user_forums")
    ) == "user:12"
    assert expected_record_id(Site("普通", "top", "https://a.test/topic/1")) == "topic:1"

    anchor = PayloadDocument("标题", "https://a.test/topic/1", '<a href="/body.php?id=2">正文</a>')
    body = PayloadDocument("正文", "https://a.test/body.php?id=2", "内容")
    assert document_is_linked(anchor, body)
    relative_anchor = PayloadDocument("相对标题", "https://a.test/bbs/topic.php?id=1", '<script src="js/app.js"></script>')
    relative_body = PayloadDocument(
        "相对脚本",
        "https://a.test/bbs/js/app.js",
        "内容",
        parent_url=relative_anchor.url,
        link_reference="js/app.js",
    )
    assert document_is_linked(relative_anchor, relative_body)
    assert not document_is_linked(anchor, anchor)
    assert not document_is_linked(anchor, PayloadDocument("错查询", "https://a.test/body.php?id=3", "内容"))
    assert not document_is_linked(anchor, PayloadDocument("错域名", "https://b.test/body.php?id=2", "内容"))


def test_cache_entry_legacy_record_fallbacks_and_invalid_values() -> None:
    entry = {
        "records": [
            {"period": 210, "zodiac": "狗蛇", "position": 0},
            {"period": None, "zodiac": "龙虎", "position": "bad"},
            "invalid",
        ],
        "article_ids": {"210": "article-a", "209": ""},
    }
    assert values_from_entry(entry) == {"210": "狗蛇"}
    assert positions_from_entry(entry) == {"210": 0}
    assert article_ids_from_entry(entry) == {"210": "article-a"}
    assert values_from_entry({}) == {}
    assert positions_from_entry({}) == {}
    assert article_ids_from_entry({}) == {}


def cache_payload(*entries: dict[str, object], issues: list[int] | None = None) -> dict[str, object]:
    return {
        "schema": 2,
        "position_kind": POSITION_KIND_VISIBLE_TEXT,
        "window_size": 10,
        "issues": issues or [210, 209, 208, 207, 206, 205, 204, 203, 202, 201],
        "sites": [
            {**entry, "position_kind": POSITION_KIND_VISIBLE_TEXT}
            for entry in entries
        ],
    }


def cache_entry(
    name: str,
    values: dict[int, str],
    positions: dict[int, int],
    *,
    article_ids: dict[int, str] | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": name,
        "url": f"https://example.test/{name}",
        "pick": "top",
        "values": {str(key): value for key, value in values.items()},
        "positions": {str(key): value for key, value in positions.items()},
    }
    if article_ids:
        entry["article_ids"] = {str(key): value for key, value in article_ids.items()}
    return entry


def test_legacy_position_cache_is_read_only_and_rejected_by_formal_gates(tmp_path: Path) -> None:
    site = Site("目录", "top", "https://example.test/目录")
    legacy = {
        "schema": 1,
        "issues": [210, 209, 208],
        "sites": [cache_entry(site.name, {210: "狗蛇"}, {210: 0})],
    }
    path = tmp_path / "legacy-cache.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="位置语义不兼容"):
        RecentCacheRepository(path).prepare_update([Result(site, Record(210, "狗蛇", "", "", 12))], 210)
    with pytest.raises(ValueError, match="位置语义不兼容"):
        guard_results_against_cache([Result(site, Record(210, "狗蛇", "", "", 12))], legacy, 210)
    with pytest.raises(ValueError, match="位置语义不兼容"):
        detect_duplicate_findings(legacy)
    with pytest.raises(ValueError, match="位置语义不兼容"):
        audit_cache_coverage(legacy, [site])

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_legacy_cache_migration_quarantines_conflicts_and_incomplete_history() -> None:
    issues = recent_periods(210)
    complete_site = Site("完整站", "top", "https://example.test/complete")
    conflict_site = Site("冲突站", "top", "https://example.test/conflict")
    failed_site = Site("缺期站", "bottom", "https://example.test/failed")
    missing_site = Site("未返回站", "top", "https://example.test/missing")
    complete_records = tuple(
        Record(period, "狗蛇", "", "", index + 100)
        for index, period in enumerate(issues)
    )
    conflict_records = tuple(
        Record(period, "龙虎", "", "", index + 200)
        for index, period in enumerate(issues)
    )
    legacy = {
        "schema": 1,
        "issues": issues,
        "sites": [
            {
                **cache_entry(complete_site.name, {period: "狗蛇" for period in issues}, {period: 0 for period in issues}),
                "url": complete_site.url,
                "pick": complete_site.pick,
            },
            {
                **cache_entry(conflict_site.name, {period: "狗蛇" for period in issues}, {period: 0 for period in issues}),
                "url": conflict_site.url,
                "pick": conflict_site.pick,
            },
            {
                **cache_entry(failed_site.name, {period: "狗蛇" for period in issues}, {period: 0 for period in issues}),
                "url": failed_site.url,
                "pick": failed_site.pick,
            },
            {
                **cache_entry(missing_site.name, {period: "狗蛇" for period in issues}, {period: 0 for period in issues}),
                "url": missing_site.url,
                "pick": missing_site.pick,
            },
        ],
    }
    results = [
        HistoryResult(complete_site, complete_records),
        HistoryResult(conflict_site, conflict_records),
        HistoryResult(failed_site, complete_records[:8], "缺少 202,201 期数据"),
    ]

    payload, conflicts = migrate_legacy_cache(legacy, results, 210)

    entries = {
        entry["name"]: entry
        for entry in cast(list[dict[str, object]], payload["sites"])
    }
    assert payload["schema"] == 2
    assert payload["position_kind"] == POSITION_KIND_VISIBLE_TEXT
    complete = cast(dict[str, object], entries["完整站"])
    conflict = cast(dict[str, object], entries["冲突站"])
    missing = cast(dict[str, object], entries["缺期站"])
    absent = cast(dict[str, object], entries["未返回站"])
    assert cast(dict[str, int], complete["positions"])["210"] == 100
    assert cast(list[dict[str, object]], complete["records"])[0]["position_kind"] == POSITION_KIND_VISIBLE_TEXT
    assert conflict["values"] == {}
    assert "生肖冲突" in cast(str, conflict["error"])
    assert missing["values"] == {}
    assert "缺少 202,201 期数据" in cast(str, missing["error"])
    assert absent["values"] == {}
    assert "未获得真实历史抓取结果" in cast(str, absent["error"])
    assert any("冲突站" in item for item in conflicts)


def test_cache_contract_rejects_mixed_record_position_kind() -> None:
    entry = cache_entry("混合", {210: "狗蛇"}, {210: 12})
    entry["records"] = [{"period": 210, "zodiac": "狗蛇", "position": 12}]
    cache = cache_payload(entry)

    with pytest.raises(ValueError, match="记录缺少统一position_kind"):
        detect_duplicate_findings(cache)


def test_recent_periods_wrap_and_repository_rejects_jump_or_bad_window(tmp_path: Path) -> None:
    assert recent_periods(2, 5) == [2, 1, 365, 364, 363]
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache_payload(issues=[210, 208, 207]), ensure_ascii=False), encoding="utf-8")
    repository = RecentCacheRepository(path)
    with pytest.raises(ValueError, match="期数窗口顺序无效"):
        repository.prepare_update([], 211)

    path.write_text(json.dumps(cache_payload(), ensure_ascii=False), encoding="utf-8")
    assert repository.prepare_update([], 212) is None

    with pytest.raises(ValueError, match="期数必须"):
        recent_periods(0)
    assert recent_periods(210, 0) == []


def test_repository_rejects_corrupt_cache_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="缓存文件损坏"):
        RecentCacheRepository(path).prepare_update([], 210)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("record", "expected_value", "expected_position", "expected_article_id"),
    [
        (Record(210, "龙虎", "", "", 0, record_id="article-a"), "龙虎", 0, "article-a"),
        (Record(210, "狗蛇", "", "", 1, record_id="article-a"), "狗蛇", 1, "article-a"),
        (Record(210, "狗蛇", "", "", 0, record_id="article-b"), "狗蛇", 0, "article-b"),
    ],
)
def test_repository_updates_cached_value_position_or_article_id_without_conflict(
    tmp_path: Path,
    record: Record,
    expected_value: str,
    expected_position: int,
    expected_article_id: str,
) -> None:
    site = Site(
        "动态站",
        "top",
        "https://example.test/article/manager/article-a?url=x",
        payload="admin_article_api",
    )
    path = tmp_path / "cache.json"
    entry = cache_entry(site.name, {210: "狗蛇"}, {210: 0}, article_ids={210: "article-a"})
    entry["url"] = site.url
    path.write_text(
        json.dumps(
            cache_payload(entry),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    repository = RecentCacheRepository(path)

    payload = repository.prepare_update([Result(site, record)], 210)

    assert payload is not None
    updated = cast(list[dict[str, object]], payload["sites"])[0]
    assert cast(dict[str, str], updated["values"])["210"] == expected_value
    assert cast(dict[str, int], updated["positions"])["210"] == expected_position
    assert cast(dict[str, str], updated["article_ids"])["210"] == expected_article_id


def test_repository_keeps_only_latest_ten_and_writes_atomically(tmp_path: Path) -> None:
    site = Site("目录", "top", "https://example.test/目录")
    old = cache_entry(site.name, {period: "狗蛇" for period in range(201, 211)}, {period: 0 for period in range(201, 211)})
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache_payload(old), ensure_ascii=False), encoding="utf-8")
    repository = RecentCacheRepository(path)

    payload = repository.prepare_update([Result(site, Record(211, "龙虎", "", "", 0))], 211)
    assert payload is not None
    assert payload["issues"] == [211, 210, 209, 208, 207, 206, 205, 204, 203, 202]
    repository.commit(payload)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema"] == 2
    assert loaded["position_kind"] == POSITION_KIND_VISIBLE_TEXT
    assert loaded["sites"][0]["position_kind"] == POSITION_KIND_VISIBLE_TEXT
    assert {record["position_kind"] for record in loaded["sites"][0]["records"]} == {
        POSITION_KIND_VISIBLE_TEXT
    }
    assert list(loaded["sites"][0]["values"]) == ["211", "210", "209", "208", "207", "206", "205", "204", "203", "202"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_repository_preserves_failed_site_identity_when_no_values_exist(tmp_path: Path) -> None:
    site = Site("失败目录", "bottom", "https://example.test/失败目录")
    entry = cache_entry(site.name, {}, {})
    entry["url"] = site.url
    entry["pick"] = site.pick
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache_payload(entry), ensure_ascii=False), encoding="utf-8")

    payload = RecentCacheRepository(path).prepare_update(
        [Result(site, None, "内容尚未发布：未找到指定期数")],
        210,
    )

    assert payload is not None
    preserved_sites = cast(list[dict[str, object]], payload["sites"])
    assert len(preserved_sites) == 1
    preserved = preserved_sites[0]
    assert preserved["name"] == site.name
    assert preserved["url"] == site.url
    assert preserved["values"] == {}
    assert preserved["positions"] == {}
    assert preserved["records"] == []
    assert preserved["error"] == "内容尚未发布：未找到指定期数"


def test_single_period_guard_converts_cache_conflict_to_failure() -> None:
    site = Site("目录", "top", "https://example.test/目录")
    cache = cache_payload(cache_entry(site.name, {210: "狗蛇"}, {210: 0}, article_ids={210: "article-a"}))
    good = Result(site, Record(210, "狗蛇", "", "", 0, record_id="article-a"))
    bad = Result(site, Record(210, "狗蛇", "", "", 1, record_id="article-a"))

    assert guard_results_against_cache([good], cache, 210)[0].ok
    rejected = guard_results_against_cache([bad], cache, 210)[0]
    assert not rejected.ok
    assert "缓存数据冲突" in rejected.error
    assert "位置0" in rejected.error and "位置1" in rejected.error


def test_guard_rejects_missing_or_changed_article_id_and_duplicate_cache_entries() -> None:
    site = Site(
        "动态",
        "top",
        "https://example.test/article/manager/article-a?url=x",
        payload="admin_article_api",
    )
    entry = cache_entry(site.name, {210: "狗蛇"}, {210: 0}, article_ids={210: "article-a"})
    entry["url"] = site.url
    cache = cache_payload(entry)
    rejected = guard_results_against_cache([Result(site, Record(210, "狗蛇", "", "", 0))], cache, 210)[0]
    assert not rejected.ok and "本次ID为缺失" in rejected.error

    duplicate = cache_payload(entry, dict(entry))
    with pytest.raises(ValueError, match="缓存文件包含重复站点"):
        guard_results_against_cache([], duplicate, 210)


def test_duplicate_detection_uses_consecutive_period_value_and_position() -> None:
    issues = [210, 209, 208, 207, 206, 205, 204, 203, 202, 201]
    base_values = {period: f"值{period}" for period in issues}
    base_positions = {period: period % 3 for period in issues}
    suspicious_values = {period: base_values[period] if period >= 206 else f"异{period}" for period in issues}
    duplicate_values = {period: base_values[period] if period >= 205 else f"异{period}" for period in issues}
    wrong_positions = dict(base_positions)
    wrong_positions[208] += 10
    wrong_positions[204] += 10
    cache = cache_payload(
        cache_entry("A", base_values, base_positions),
        cache_entry("B", suspicious_values, base_positions),
        cache_entry("C", duplicate_values, base_positions),
        cache_entry("D", base_values, wrong_positions),
        issues=issues,
    )

    findings = detect_duplicate_findings(cache)
    by_pair = {(finding.first, finding.second): finding for finding in findings}
    assert by_pair[("A", "B")].consecutive == 5
    assert by_pair[("A", "B")].classification == "suspicious"
    assert by_pair[("A", "C")].consecutive == 6
    assert by_pair[("A", "C")].classification == "duplicate"
    assert by_pair[("A", "D")].consecutive < 6


def test_one_or_two_matching_periods_are_not_reported() -> None:
    issues = [210, 209, 208, 207]
    cache = cache_payload(
        cache_entry("A", {210: "狗蛇", 209: "龙虎", 208: "牛马"}, {210: 0, 209: 1, 208: 2}),
        cache_entry("B", {210: "狗蛇", 209: "龙虎", 208: "鸡羊"}, {210: 0, 209: 1, 208: 2}),
        issues=issues,
    )
    assert detect_duplicate_findings(cache) == []


def test_duplicate_detection_resets_on_missing_position() -> None:
    issues = [210, 209, 208, 207, 206]
    values = {period: "狗蛇" for period in issues}
    left_positions = {period: 0 for period in issues}
    right_positions = dict(left_positions)
    right_positions.pop(208)
    cache = cache_payload(
        cache_entry("A", values, left_positions),
        cache_entry("B", values, right_positions),
        issues=issues,
    )

    assert detect_duplicate_findings(cache) == []


def test_schema_two_cache_coverage_requires_ten_versioned_positions() -> None:
    issues = list(range(210, 200, -1))
    complete_values = {period: "狗蛇" for period in issues}
    complete_positions = {period: period for period in issues}
    incomplete_positions = dict(complete_positions)
    incomplete_positions.pop(205)
    cache = cache_payload(
        cache_entry("完整", complete_values, complete_positions),
        cache_entry("不完整", complete_values, incomplete_positions),
        issues=issues,
    )
    sites = [
        Site("完整", "top", "https://example.test/完整"),
        Site("不完整", "top", "https://example.test/不完整"),
        Site("缺失", "top", "https://example.test/缺失"),
    ]

    coverage = audit_cache_coverage(cache, sites)

    assert coverage.missing_sites == ("缺失",)
    assert coverage.incomplete_sites == ("不完整",)


def test_schema_two_repository_advances_from_365_to_001_without_mixing_positions(tmp_path: Path) -> None:
    site = Site("目录", "top", "https://example.test/目录")
    issues = [365, 364, 363, 362, 361, 360, 359, 358, 357, 356]
    old = cache_entry(
        site.name,
        {period: "狗蛇" for period in issues},
        {period: period for period in issues},
    )
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache_payload(old, issues=issues), ensure_ascii=False), encoding="utf-8")

    payload = RecentCacheRepository(path).prepare_update(
        [Result(site, Record(1, "龙虎", "", "", 12))],
        1,
    )

    assert payload is not None
    assert payload["issues"] == [1, 365, 364, 363, 362, 361, 360, 359, 358, 357]
    assert payload["schema"] == 2
    assert payload["position_kind"] == POSITION_KIND_VISIBLE_TEXT


def test_baseline_cache_audit_is_read_only_and_deterministic() -> None:
    cache_path = BASELINE / "recent_10_cache.json"
    before = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    cache = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    sites = load_sites(ROOT / "config" / "sites.json")

    with pytest.raises(ValueError, match="位置语义不兼容"):
        audit_cache_coverage(cache, sites)
    with pytest.raises(ValueError, match="位置语义不兼容"):
        detect_duplicate_findings(cache)
    assert hashlib.sha256(cache_path.read_bytes()).hexdigest() == before


def test_validation_and_cache_layers_have_no_forbidden_dependencies() -> None:
    checks = {
        "validation": {"fetching", "cache", "services", "output"},
        "cache": {"fetching", "parsers", "services", "output"},
    }
    violations: list[str] = []
    for package, forbidden in checks.items():
        for path in (ROOT / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots = {node.module.split(".", 1)[0]}
                else:
                    continue
                for root in roots & forbidden:
                    violations.append(f"{package}/{path.name}:{node.lineno}:{root}")
    assert violations == []
