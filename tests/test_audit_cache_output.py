from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import cache.duplicates as duplicate_cache
import cli
from cache.contracts import (
    CACHE_POSITION_KIND,
    CACHE_SCHEMA_VERSION,
    config_fingerprint,
    recent_periods,
    validate_cache_position_contract,
)
from cache.duplicates import (
    article_ids_from_entry,
    audit_cache_coverage,
    detect_duplicate_findings,
    positions_from_entry,
    values_from_entry,
)
from cache.repository import RecentCacheRepository
from domain.models import HistoryResult, Record, Result, Site
from output import transaction


def _site(name: str, *, pick: str = "top", payload: str = "page") -> Site:
    return Site(
        name=name, url=f"https://{name}.example.test", pick=pick, payload=payload
    )


def _record(
    period: int, zodiac: str = "牛马", *, position: int = 7, record_id: str = ""
) -> Record:
    return Record(
        period=period,
        zodiac=zodiac,
        open_result="",
        raw=zodiac,
        position=position,
        record_id=record_id,
    )


def _result(
    site: Site, period: int, *, zodiac: str = "牛马", position: int = 7
) -> Result:
    return Result(site=site, record=_record(period, zodiac, position=position))


def _cache_entry(
    site: Site, period: int, *, zodiac: str = "鼠虎", position: int = 3
) -> dict[str, object]:
    key = str(period)
    return {
        "name": site.name,
        "url": site.url,
        "pick": site.pick,
        "position_kind": CACHE_POSITION_KIND,
        "values": {key: zodiac},
        "positions": {key: position},
        "article_ids": {key: "old-article"},
        "records": [
            {
                "period": period,
                "zodiac": zodiac,
                "position": position,
                "position_kind": CACHE_POSITION_KIND,
            }
        ],
    }


def _window_entry(
    site: Site,
    issues: list[int],
    *,
    zodiac: str = "鼠虎",
    position: int = 3,
    missing: set[int] | None = None,
) -> dict[str, object]:
    missing = missing or set()
    periods = [period for period in issues if period not in missing]
    return {
        "name": site.name,
        "url": site.url,
        "pick": site.pick,
        "position_kind": CACHE_POSITION_KIND,
        "values": {str(period): zodiac for period in periods},
        "positions": {str(period): position for period in periods},
        "records": [
            {
                "period": period,
                "zodiac": zodiac,
                "position": position,
                "position_kind": CACHE_POSITION_KIND,
            }
            for period in periods
        ],
    }


def _duplicate_cache(
    *,
    period: int = 100,
    matching_periods: set[int] | None = None,
) -> dict[str, object]:
    sites = [_site("duplicate-left"), _site("duplicate-right", pick="bottom")]
    cache = _cache([], period=period, fingerprint_sites=sites)
    cache["issues"] = recent_periods(period)
    issues = cache["issues"]
    matching_periods = set(issues) if matching_periods is None else matching_periods
    left = _window_entry(sites[0], issues)
    right = _window_entry(sites[1], issues, zodiac="鼠蛇")
    for issue in matching_periods:
        right["values"][str(issue)] = "鼠虎"
        next(record for record in right["records"] if record["period"] == issue)[
            "zodiac"
        ] = "鼠虎"
    cache["sites"] = [left, right]
    return cache


def _cache(
    entries: list[dict[str, object]],
    period: int = 100,
    *,
    fingerprint_sites: list[Site] | None = None,
) -> dict[str, object]:
    cache: dict[str, object] = {
        "schema": CACHE_SCHEMA_VERSION,
        "position_kind": CACHE_POSITION_KIND,
        "window_size": 10,
        "config_fingerprint": "0" * 64,
        "issues": [period, *range(period - 1, period - 10, -1)],
        "sites": entries,
    }
    if fingerprint_sites is not None:
        cache["config_fingerprint"] = config_fingerprint(fingerprint_sites)
    return cache


def _write_cache(path: Path, cache: dict[str, object]) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def test_prepare_update_failed_result_removes_current_period_data(
    tmp_path: Path,
) -> None:
    site = _site("old")
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path, _cache([_cache_entry(site, 100)], fingerprint_sites=[site])
    )
    repository = RecentCacheRepository(cache_path)

    failed = Result(site=site, error="指定期缺失")
    prepared = repository.prepare_update([failed], 100)

    assert prepared is not None
    entry = prepared["sites"][0]
    assert entry["values"] == {}
    assert entry["positions"] == {}
    assert "article_ids" not in entry
    assert entry["records"] == []
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    ("cache_sites", "results"),
    [
        (["a", "b"], ["a"]),
        (["a"], ["a", "b"]),
        (["a", "b"], ["b", "a"]),
    ],
)
def test_prepare_update_rejects_existing_cache_identity_mismatch(
    tmp_path: Path,
    cache_sites: list[str],
    results: list[str],
) -> None:
    sites = {name: _site(name) for name in ("a", "b")}
    entries = [_cache_entry(sites[name], 100) for name in cache_sites]
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        _cache(entries, fingerprint_sites=[sites[name] for name in results]),
    )
    repository = RecentCacheRepository(cache_path)

    with pytest.raises(ValueError, match="身份|顺序|站点"):
        repository.prepare_update([_result(sites[name], 100) for name in results], 100)


def test_prepare_update_rejects_duplicate_existing_cache_identity(
    tmp_path: Path,
) -> None:
    site = _site("duplicate")
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, _cache([_cache_entry(site, 100), _cache_entry(site, 100)]))

    with pytest.raises(ValueError, match="重复"):
        RecentCacheRepository(cache_path).prepare_update([_result(site, 100)], 100)


def test_prepare_update_initializes_truly_new_empty_cache_in_result_order(
    tmp_path: Path,
) -> None:
    first = _site("first")
    second = _site("second", pick="bottom")

    prepared = RecentCacheRepository(tmp_path / "cache.json").prepare_update(
        [_result(first, 100), _result(second, 100)],
        100,
    )

    assert prepared is not None
    assert [(entry["name"], entry["pick"]) for entry in prepared["sites"]] == [
        ("first", "top"),
        ("second", "bottom"),
    ]


def test_prepare_history_update_does_not_inherit_failed_or_missing_sites(
    tmp_path: Path,
) -> None:
    old_site = _site("old", payload="admin_article_api")
    missing_site = _site("missing")
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path,
        _cache([_cache_entry(old_site, 100), _cache_entry(missing_site, 100)]),
    )
    failed = HistoryResult(site=old_site, records=(), error="历史抓取失败")

    prepared = RecentCacheRepository(cache_path).prepare_history_update([failed], 100)

    assert [entry["name"] for entry in prepared["sites"]] == ["old"]
    entry = prepared["sites"][0]
    assert entry["values"] == {}
    assert entry["positions"] == {}
    assert "article_ids" not in entry
    assert entry["records"] == []
    assert entry["status"] == "failed"


def test_prepare_history_update_keeps_partial_live_records_on_error_without_old_periods(
    tmp_path: Path,
) -> None:
    site = _site("history-partial", payload="admin_article_api")
    cache_path = tmp_path / "cache.json"
    _write_cache(
        cache_path, _cache([_cache_entry(site, 99, zodiac="旧值", position=2)])
    )

    result = HistoryResult(
        site=site,
        records=(_record(100, zodiac="新值", position=8, record_id="live-100"),),
        error="缺少99期",
    )
    prepared = RecentCacheRepository(cache_path).prepare_history_update([result], 100)

    entry = prepared["sites"][0]
    assert entry["status"] == "failed"
    assert entry["error"] == "缺少99期"
    assert entry["values"] == {"100": "新值"}
    assert entry["positions"] == {"100": 8}
    assert entry["article_ids"] == {"100": "live-100"}
    assert [record["period"] for record in entry["records"]] == [100]


def test_prepare_history_update_deduplicates_identical_period_records(
    tmp_path: Path,
) -> None:
    site = _site("history-duplicate")
    record = _record(100, zodiac="新值", position=8)
    result = HistoryResult(site=site, records=(record, record))

    prepared = RecentCacheRepository(tmp_path / "cache.json").prepare_history_update(
        [result], 100
    )

    entry = prepared["sites"][0]
    assert entry["values"] == {"100": "新值"}
    assert entry["positions"] == {"100": 8}
    assert len(entry["records"]) == 1


def test_prepare_history_update_deduplicates_normalized_period_records(
    tmp_path: Path,
) -> None:
    site = _site("history-normalized-duplicate")
    result = HistoryResult(
        site=site,
        records=(
            _record(100, zodiac="鼠-虎", position=8),
            _record(100, zodiac="鼠虎", position=8),
        ),
    )

    prepared = RecentCacheRepository(tmp_path / "cache.json").prepare_history_update(
        [result], 100
    )

    entry = prepared["sites"][0]
    assert entry["values"] == {"100": "鼠-虎"}
    assert entry["positions"] == {"100": 8}
    assert len(entry["records"]) == 1


def test_prepare_history_update_rejects_conflicting_article_ids(tmp_path: Path) -> None:
    site = _site("history-article-id-conflict", payload="admin_article_api")
    result = HistoryResult(
        site=site,
        records=(
            _record(100, zodiac="鼠虎", position=8, record_id="article-1"),
            _record(100, zodiac="鼠-虎", position=8, record_id="article-2"),
        ),
    )

    with pytest.raises(ValueError, match="冲突|record_id"):
        RecentCacheRepository(tmp_path / "cache.json").prepare_history_update(
            [result], 100
        )


@pytest.mark.parametrize(
    ("zodiac", "position"),
    [("不同值", 8), ("新值", 9)],
)
def test_prepare_history_update_rejects_conflicting_period_records(
    tmp_path: Path,
    zodiac: str,
    position: int,
) -> None:
    site = _site("history-conflict")
    result = HistoryResult(
        site=site,
        records=(
            _record(100, zodiac="新值", position=8),
            _record(100, zodiac=zodiac, position=position),
        ),
    )

    with pytest.raises(ValueError, match="冲突"):
        RecentCacheRepository(tmp_path / "cache.json").prepare_history_update(
            [result], 100
        )


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "", "url": "https://a.test", "pick": "top"},
        {"name": "a", "url": "", "pick": "top"},
        {"name": "a", "url": "https://a.test", "pick": "middle"},
    ],
)
def test_validate_cache_position_contract_rejects_invalid_identity(
    entry: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="身份|name|url|pick"):
        validate_cache_position_contract(_cache([entry]))


def test_validate_cache_position_contract_rejects_duplicate_identity() -> None:
    site = _site("same")
    with pytest.raises(ValueError, match="重复"):
        validate_cache_position_contract(
            _cache([_cache_entry(site, 100), _cache_entry(site, 99)])
        )


def test_validate_cache_position_contract_keeps_position_kind_check() -> None:
    site = _site("position")
    entry = _cache_entry(site, 100)
    entry["records"][0]["position_kind"] = "candidate_index_v1"

    with pytest.raises(ValueError, match="position_kind"):
        validate_cache_position_contract(_cache([entry]))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda entry: entry["positions"].pop("100"),
        lambda entry: entry["positions"].update({"99": 3}),
        lambda entry: entry["records"].clear(),
        lambda entry: entry["records"][0].update({"position": -1}),
        lambda entry: entry["records"][0].update({"zodiac": "兔龙"}),
    ],
)
def test_validate_cache_position_contract_rejects_position_or_record_gap(
    mutate,
) -> None:
    site = _site("position-gap")
    entry = _cache_entry(site, 100)
    mutate(entry)

    with pytest.raises(ValueError, match="position|期数|records|values"):
        validate_cache_position_contract(_cache([entry], fingerprint_sites=[site]))


@pytest.mark.parametrize("field", ["values", "records"])
@pytest.mark.parametrize("zodiac", ["鼠", "鼠鼠", "鼠A"])
def test_validate_cache_position_contract_rejects_invalid_zodiac(
    field: str, zodiac: str
) -> None:
    site = _site("invalid-zodiac")
    entry = _cache_entry(site, 100)
    if field == "values":
        entry["values"] = {"100": zodiac}
    else:
        entry["records"][0]["zodiac"] = zodiac

    with pytest.raises(ValueError, match="生肖|zodiac|合法"):
        validate_cache_position_contract(_cache([entry], fingerprint_sites=[site]))


def test_validate_cache_position_contract_allows_missing_periods() -> None:
    site = _site("empty-periods")
    entry = {
        "name": site.name,
        "url": site.url,
        "pick": site.pick,
        "position_kind": CACHE_POSITION_KIND,
        "values": {},
        "positions": {},
        "records": [],
    }

    validate_cache_position_contract(_cache([entry], fingerprint_sites=[site]))


def test_validate_cache_position_contract_rejects_missing_sites() -> None:
    cache = _cache([], fingerprint_sites=[])
    cache.pop("sites")

    with pytest.raises(ValueError, match="sites|站点列表"):
        validate_cache_position_contract(cache)


@pytest.mark.parametrize("bad_period", ["101", "abc", "0", "366"])
def test_validate_cache_position_contract_rejects_nonwindow_period_semantics(
    bad_period: str,
) -> None:
    site = _site("period-contract")
    entry = _cache_entry(site, 100)
    entry["values"] = {bad_period: "鼠虎"}
    entry["positions"] = {bad_period: 3}
    entry["records"] = [
        {
            "period": bad_period,
            "zodiac": "鼠虎",
            "position": 3,
            "position_kind": CACHE_POSITION_KIND,
        }
    ]

    with pytest.raises(ValueError, match="期号|窗口|period"):
        validate_cache_position_contract(_cache([entry], fingerprint_sites=[site]))


@pytest.mark.parametrize(
    "change",
    [
        lambda cache: cache.update({"window_size": 9}),
        lambda cache: cache.update({"issues": cache["issues"][:-1]}),
        lambda cache: cache.update(
            {"issues": [100, 99, 98, 97, 96, 95, 94, 93, 92, 92]}
        ),
        lambda cache: cache.update(
            {"issues": [100, 99, 98, 97, 96, 95, 94, 93, 91, 92]}
        ),
    ],
)
def test_validate_cache_position_contract_rejects_invalid_root_window_or_fingerprint(
    change,
) -> None:
    site = _site("root-contract")
    cache = _cache([_cache_entry(site, 100)], fingerprint_sites=[site])
    change(cache)

    with pytest.raises(ValueError, match="window_size|期数窗口|config_fingerprint"):
        validate_cache_position_contract(cache)


def test_prepare_update_accepts_same_config_and_persists_fingerprint(
    tmp_path: Path,
) -> None:
    site = _site("fingerprint", payload="admin_article_api")
    repository = RecentCacheRepository(tmp_path / "cache.json")
    initial = repository.prepare_update([_result(site, 100)], 100)
    assert initial is not None
    repository.commit(initial)

    repeated = repository.prepare_update([_result(site, 100)], 100)

    assert repeated is not None
    assert repeated["config_fingerprint"] == initial["config_fingerprint"]


@pytest.mark.parametrize(
    "changed_sites",
    [
        lambda sites: [replace(sites[0], parser="other_parser"), sites[1]],
        lambda sites: [replace(sites[0], title="other title"), sites[1]],
        lambda sites: [replace(sites[0], record="other record"), sites[1]],
        lambda sites: [sites[1], sites[0]],
    ],
)
def test_prepare_update_allows_config_fingerprint_change(
    tmp_path: Path, changed_sites
) -> None:
    sites = [_site("fingerprint-a"), _site("fingerprint-b", pick="bottom")]
    repository = RecentCacheRepository(tmp_path / "cache.json")
    initial = repository.prepare_update([_result(site, 100) for site in sites], 100)
    assert initial is not None
    repository.commit(initial)

    assert repository.prepare_update(
        [_result(site, 100) for site in changed_sites(sites)], 100
    ) is not None


def test_prepare_history_update_rebuilds_config_fingerprint(tmp_path: Path) -> None:
    old_site = _site("history-fingerprint")
    rebuilt_site = replace(old_site, parser="history_parser")
    repository = RecentCacheRepository(tmp_path / "cache.json")
    initial = repository.prepare_update([_result(old_site, 100)], 100)
    assert initial is not None
    repository.commit(initial)

    rebuilt = repository.prepare_history_update(
        [HistoryResult(rebuilt_site, (_record(100),))],
        100,
    )

    assert rebuilt["config_fingerprint"] != initial["config_fingerprint"]
    assert rebuilt["sites"][0]["name"] == rebuilt_site.name


def test_prepare_history_update_rebuilds_over_invalid_old_positions(
    tmp_path: Path,
) -> None:
    site = _site("history-invalid-old")
    entry = _cache_entry(site, 100)
    entry["positions"] = {}
    entry["records"][0]["position"] = -1
    cache_path = tmp_path / "cache.json"
    _write_cache(cache_path, _cache([entry], fingerprint_sites=[site]))

    rebuilt = RecentCacheRepository(cache_path).prepare_history_update(
        [HistoryResult(site, (_record(100, position=8),))],
        100,
    )

    output_entry = rebuilt["sites"][0]
    assert output_entry["positions"] == {"100": 8}
    assert output_entry["records"][0]["position"] == 8


@pytest.mark.parametrize("prepare_history", [False, True])
def test_commit_rejects_external_cache_change_after_prepare(
    tmp_path: Path, prepare_history: bool
) -> None:
    site = _site("compare-and-swap", payload="admin_article_api")
    repository = RecentCacheRepository(tmp_path / "cache.json")
    if prepare_history:
        prepared = repository.prepare_history_update(
            [HistoryResult(site, (_record(100, record_id="history-id"),))],
            100,
        )
    else:
        prepared = repository.prepare_update([_result(site, 100)], 100)
    assert prepared is not None

    repository.path.write_text("external writer won", encoding="utf-8")

    with pytest.raises(ValueError, match="变化|准备"):
        repository.commit(prepared)
    assert repository.path.read_text(encoding="utf-8") == "external writer won"


def test_commit_rejects_busy_cache_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = RecentCacheRepository(tmp_path / "cache.json")
    prepared = repository.prepare_update([_result(_site("busy-lock"), 100)], 100)
    assert prepared is not None
    if os.name == "nt":
        import msvcrt

        monkeypatch.setattr(
            msvcrt, "locking", lambda *args: (_ for _ in ()).throw(OSError("busy"))
        )
    else:
        import fcntl

        monkeypatch.setattr(
            fcntl, "flock", lambda *args: (_ for _ in ()).throw(OSError("busy"))
        )

    with pytest.raises(ValueError, match="缓存正在被其他进程写入"):
        repository.commit(prepared)


def test_commit_rejects_direct_mapping_even_when_cache_missing(tmp_path: Path) -> None:
    repository = RecentCacheRepository(tmp_path / "cache.json")
    with pytest.raises(ValueError, match="prepare|拒绝"):
        repository.commit({"direct": True})
    assert not repository.path.exists()


def test_commit_rejects_invalid_prepared_payload_without_writing(
    tmp_path: Path,
) -> None:
    repository = RecentCacheRepository(tmp_path / "cache.json")
    prepared = repository.prepare_update([_result(_site("invalid-prepared"), 100)], 100)
    assert prepared is not None
    prepared["sites"][0]["values"]["100"] = "无效"

    with pytest.raises(ValueError, match="zodiac|合法"):
        repository.commit(prepared)
    assert not repository.path.exists()


@pytest.mark.parametrize("period", [0, 366, True])
def test_prepare_rejects_invalid_current_period_for_update_and_history(
    tmp_path: Path,
    period: int,
) -> None:
    site = _site("invalid-current-period")
    repository = RecentCacheRepository(tmp_path / "cache.json")

    with pytest.raises(ValueError, match="期数"):
        repository.prepare_update([_result(site, 100)], period)
    with pytest.raises(ValueError, match="期数"):
        repository.prepare_history_update([HistoryResult(site, ())], period)
    assert not repository.path.exists()


def test_duplicate_check_rejects_cache_order_mismatch(tmp_path: Path) -> None:
    sites = [_site("coverage-a"), _site("coverage-b", pick="bottom")]
    repository = RecentCacheRepository(tmp_path / "cache.json")
    prepared = repository.prepare_update([_result(site, 100) for site in sites], 100)
    assert prepared is not None

    with pytest.raises(ValueError, match="身份或顺序"):
        audit_cache_coverage(prepared, list(reversed(sites)))


def test_duplicate_check_rejects_cache_fingerprint_mismatch(tmp_path: Path) -> None:
    site = _site("coverage-fingerprint")
    repository = RecentCacheRepository(tmp_path / "cache.json")
    prepared = repository.prepare_update([_result(site, 100)], 100)
    assert prepared is not None
    prepared["config_fingerprint"] = "f" * 64

    with pytest.raises(ValueError, match="config_fingerprint"):
        audit_cache_coverage(prepared, [site])


def test_duplicate_entry_extractors_cover_values_positions_records_fallbacks() -> None:
    assert values_from_entry({"values": {"100": "鼠虎", "101": ""}}) == {"100": "鼠虎"}
    assert positions_from_entry(
        {"positions": {"100": "3", "101": "bad", "102": None}}
    ) == {"100": 3}
    assert article_ids_from_entry({"article_ids": {"100": "article", "101": ""}}) == {
        "100": "article"
    }

    records = [
        {"period": 100, "zodiac": "鼠虎", "position": "3"},
        {"period": None, "zodiac": "牛马", "position": 4},
        {"period": 101, "zodiac": "", "position": "bad"},
        {"period": 102, "zodiac": "", "position": None},
        "not-a-record",
    ]
    assert values_from_entry({"records": records}) == {"100": "鼠虎"}
    assert positions_from_entry({"records": records}) == {"100": 3}
    assert values_from_entry({"records": "not-a-list"}) == {}
    assert positions_from_entry({"records": "not-a-list"}) == {}
    assert article_ids_from_entry({"article_ids": "not-a-dict"}) == {}


@pytest.mark.parametrize(
    ("matching_count", "classification"),
    [(3, "suspicious"), (5, "suspicious"), (6, "duplicate")],
)
def test_detect_duplicate_findings_classifies_matching_thresholds(
    matching_count: int,
    classification: str,
) -> None:
    all_matches = _duplicate_cache()
    issues = list(all_matches["issues"])
    cache = _duplicate_cache(matching_periods=set(issues[:matching_count]))

    findings = detect_duplicate_findings(cache)

    assert len(findings) == 1
    assert findings[0].consecutive == matching_count
    assert findings[0].classification == classification
    assert findings[0].start_period == issues[0]
    assert findings[0].end_period == issues[matching_count - 1]


def test_detect_duplicate_findings_resets_disconnected_matching_run() -> None:
    all_matches = _duplicate_cache()
    issues = list(all_matches["issues"])
    cache = _duplicate_cache(matching_periods=set(issues[:2] + issues[3:6]))

    findings = detect_duplicate_findings(cache)

    assert len(findings) == 1
    assert findings[0].consecutive == 3
    assert findings[0].start_period == issues[3]
    assert findings[0].end_period == issues[5]
    assert findings[0].classification == "suspicious"


def test_detect_duplicate_findings_handles_cross_year_window() -> None:
    findings = detect_duplicate_findings(_duplicate_cache(period=2))

    assert len(findings) == 1
    assert findings[0].start_period == 2
    assert findings[0].end_period == 358
    assert findings[0].consecutive == 10
    assert findings[0].classification == "duplicate"


def test_detect_duplicate_findings_returns_empty_without_matches() -> None:
    assert detect_duplicate_findings(_duplicate_cache(matching_periods=set())) == []


def test_detect_duplicate_findings_rejects_position_mismatch_for_one_period() -> None:
    cache = _duplicate_cache()
    issues = list(cache["issues"])
    right = cache["sites"][1]
    key = str(issues[0])
    right["positions"][key] = 4
    next(record for record in right["records"] if record["period"] == issues[0])[
        "position"
    ] = 4

    findings = detect_duplicate_findings(cache)

    assert findings[0].consecutive == 9


def test_detect_duplicate_findings_allows_missing_period_on_one_site() -> None:
    cache = _duplicate_cache()
    issues = list(cache["issues"])
    right = cache["sites"][1]
    key = str(issues[0])
    right["values"].pop(key)
    right["positions"].pop(key)
    right["records"] = [
        record for record in right["records"] if record["period"] != issues[0]
    ]

    findings = detect_duplicate_findings(cache)

    assert findings[0].consecutive == 9


def test_audit_cache_coverage_reports_normal_and_incomplete() -> None:
    sites = [_site("coverage-complete"), _site("coverage-incomplete", pick="bottom")]
    cache = _cache([], fingerprint_sites=sites)
    issues = list(cache["issues"])
    cache["sites"] = [
        _window_entry(sites[0], issues),
        _window_entry(sites[1], issues, missing={issues[-1]}),
    ]

    coverage = audit_cache_coverage(cache, sites)

    assert coverage.missing_sites == ()
    assert coverage.incomplete_sites == (sites[1].name,)

    cache["sites"] = [_window_entry(site, issues) for site in sites]
    coverage = audit_cache_coverage(cache, sites)
    assert coverage.missing_sites == ()
    assert coverage.incomplete_sites == ()


def test_audit_cache_coverage_reports_missing_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = _site("coverage-missing")
    cache = _cache([_window_entry(site, [100])], fingerprint_sites=[site])
    calls = 0

    def mismatching_lookup(name: str, url: str, pick: str) -> tuple[str, str, str]:
        nonlocal calls
        calls += 1
        return (name, url, pick) if calls == 1 else ("missing", url, pick)

    monkeypatch.setattr(duplicate_cache, "cache_key", mismatching_lookup)

    coverage = audit_cache_coverage(cache, [site])

    assert coverage.missing_sites == (site.name,)
    assert coverage.incomplete_sites == ()


@pytest.mark.parametrize("raw_sites", [{}, [object()]])
def test_audit_cache_coverage_rejects_structure_errors(
    monkeypatch: pytest.MonkeyPatch,
    raw_sites: object,
) -> None:
    monkeypatch.setattr(
        duplicate_cache, "validate_cache_position_contract", lambda _cache: None
    )

    with pytest.raises(ValueError, match="站点结构|站点列表"):
        audit_cache_coverage({"sites": raw_sites}, [])


def test_cli_duplicate_check_rejects_cache_order_mismatch(
    tmp_path: Path, capsys
) -> None:
    sites = [_site("cli-coverage-a"), _site("cli-coverage-b", pick="bottom")]
    prepared = RecentCacheRepository(tmp_path / "cache.json").prepare_update(
        [_result(site, 100) for site in sites],
        100,
    )
    assert prepared is not None
    cache_path = tmp_path / "duplicate-check.json"
    _write_cache(cache_path, prepared)

    assert cli.run_duplicate_check(list(reversed(sites)), cache_path) == 2
    assert "身份或顺序" in capsys.readouterr().out


def test_cli_duplicate_check_rejects_non_utf8_cache(tmp_path: Path, capsys) -> None:
    cache_path = tmp_path / "invalid-encoding.json"
    cache_path.write_bytes(b"\xff\xfe\x00")

    assert cli.run_duplicate_check([_site("non-utf8")], cache_path) == 2
    output = capsys.readouterr().out
    assert "重复检测缓存读取失败" in output
    assert "Traceback" not in output


def test_cli_main_reports_site_config_load_failure(tmp_path: Path, capsys) -> None:
    exit_code = cli.main(
        [
            "--period",
            "100",
            "--sites-file",
            str(tmp_path / "missing-sites.json"),
        ]
    )

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "站点配置加载失败" in output
    assert "Traceback" not in output


def test_cli_main_reports_site_parser_binding_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    site = replace(_site("unknown-parser"), parser="not-registered")
    monkeypatch.setattr(cli, "load_sites", lambda path, allowed_parsers: [site])

    exit_code = cli.main(
        [
            "--period",
            "100",
            "--sites-file",
            str(tmp_path / "sites.json"),
        ]
    )

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "站点配置加载失败" in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--timeout", "0"),
        ("--timeout", "-1"),
        ("--workers", "0"),
        ("--workers", "-1"),
        ("--limit", "-1"),
    ],
)
def test_parse_args_rejects_nonpositive_runtime_values(option: str, value: str) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--period", "001", option, value])


def test_output_pair_rolls_back_when_failure_txt_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site("output")
    result = _result(site, 100)
    failed = Result(site=_site("failed"), error="网络错误")
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"
    output.write_text("old success", encoding="utf-8")
    errors.write_text("old failure", encoding="utf-8")
    original = transaction.atomic_write_text

    def fail_failure_file(path: Path, value: str) -> None:
        if path == errors:
            raise OSError("模拟失败TXT写入失败")
        original(path, value)

    monkeypatch.setattr(transaction, "atomic_write_text", fail_failure_file)

    with pytest.raises(OSError, match="失败TXT"):
        transaction.write_outputs([result, failed], output, errors, include_url=False)

    assert output.read_text(encoding="utf-8") == "old success"
    assert errors.read_text(encoding="utf-8") == "old failure"


def test_repair_success_only_appends_missing_lines(tmp_path: Path) -> None:
    output = tmp_path / "success.txt"
    original_text = "牛马 原有站点\n黄杀\n有点帅\n金绝\n金元宝\n男人牛\n\n生肖次数排行榜\n牛 1次\n马 1次\n"
    original = original_text.encode()
    expected = original_text.replace("黄杀\n", "猪狗 修复站点\n黄杀\n").encode()
    output.write_bytes(original)
    repaired = _result(_site("修复站点"), 238, zodiac="猪狗")

    assert (
        transaction.append_repaired_successes([repaired], output, include_url=False)
        == 1
    )
    assert output.read_bytes() == expected
    assert (
        transaction.append_repaired_successes([repaired], output, include_url=False)
        == 0
    )
    assert output.read_bytes() == expected


def test_success_ranking_uses_columns_and_dense_ranks() -> None:
    results = [
        _result(_site("站点一"), 241, zodiac="狗龙"),
        _result(_site("站点二"), 241, zodiac="狗马"),
    ]

    success, failure = transaction.format_results(results, include_url=False)

    assert failure == ""
    assert success.endswith("内容\t次数\t排名\n狗\t2\t1\n马\t1\t2\n龙\t1\t2\n")
    assert not any(
        line in success.splitlines()
        for line in ("黄杀", "有点帅", "金绝", "金元宝", "男人牛")
    )


def test_repair_success_rejects_failed_result(tmp_path: Path) -> None:
    failed = Result(site=_site("仍失败"), error="指定期缺失")

    with pytest.raises(ValueError, match="修复结果尚未成功"):
        transaction.append_repaired_successes(
            [failed], tmp_path / "success.txt", include_url=False
        )


def test_cache_commit_failure_keeps_new_output_pair(tmp_path: Path) -> None:
    site = _site("success")
    failed = Result(site=_site("failed"), error="指定期缺失")
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    cache.write_text("old cache", encoding="utf-8")

    class FailingRepository:
        path = cache

        def commit(self, payload: dict[str, object]) -> None:
            raise OSError("磁盘不可写")

    with pytest.raises(transaction.CacheUpdateError, match="缓存更新未完成"):
        transaction.write_formal_outputs_and_cache(
            [_result(site, 100), failed],
            output,
            errors,
            FailingRepository(),
            {"new": True},
            include_url=False,
        )

    assert "牛马 success" in output.read_text(encoding="utf-8")
    assert "failed top" in errors.read_text(encoding="utf-8")
    assert cache.read_text(encoding="utf-8") == "old cache"


@pytest.mark.parametrize("collision", ["output", "errors", "cache"])
def test_formal_transaction_rejects_path_collisions(
    tmp_path: Path, collision: str
) -> None:
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    if collision == "output":
        errors = output
    elif collision in {"errors", "cache"}:
        cache = errors

    class Repository:
        path = cache

    with pytest.raises(ValueError, match="路径冲突"):
        transaction.write_formal_outputs_and_cache(
            [_result(_site("collision"), 100)],
            output,
            errors,
            Repository(),
            None,
            include_url=False,
        )


def test_cli_cache_prepare_failure_still_writes_txt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    site = _site("cli")
    result = _result(site, 100)
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"

    class PrepareFailRepository:
        def __init__(self, path: Path) -> None:
            self.path = path

        def prepare_update(self, results, current_period):
            raise ValueError("缓存身份顺序不一致")

    monkeypatch.setattr(cli, "RecentCacheRepository", PrepareFailRepository)
    monkeypatch.setattr(cli, "load_sites", lambda path, allowed_parsers: [site])
    monkeypatch.setattr(
        cli,
        "scrape_sites",
        lambda sites, period, timeout, workers, registry: [result],
    )

    exit_code = cli.main(
        [
            "--period",
            "100",
            "--sites-file",
            str(tmp_path / "sites.json"),
            "--output",
            str(output),
            "--errors",
            str(errors),
            "--history-cache-file",
            str(cache),
        ]
    )

    assert exit_code == 2
    assert "牛马 cli" in output.read_text(encoding="utf-8")
    assert not errors.exists()
    captured = capsys.readouterr().out
    assert "缓存更新未完成" in captured
    assert captured.count("缓存更新未完成") == 1
    assert "成功 1 条，失败 0 条" in captured


def test_cli_skipped_cache_period_reports_noncontiguous_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    site = _site("cli-skipped")
    result = _result(site, 100)
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"

    class SkipRepository:
        def __init__(self, path: Path) -> None:
            self.path = path

        def prepare_update(self, results, current_period):
            return None

    monkeypatch.setattr(cli, "RecentCacheRepository", SkipRepository)
    monkeypatch.setattr(cli, "load_sites", lambda path, allowed_parsers: [site])
    monkeypatch.setattr(
        cli, "scrape_sites", lambda sites, period, timeout, workers, registry: [result]
    )

    exit_code = cli.main(
        [
            "--period",
            "100",
            "--sites-file",
            str(tmp_path / "sites.json"),
            "--output",
            str(output),
            "--errors",
            str(errors),
            "--history-cache-file",
            str(tmp_path / "cache.json"),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert "当前期数与缓存窗口不连续，缓存未更新" in capsys.readouterr().out
