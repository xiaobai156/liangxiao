from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import requests

import cli
from cache.repository import RecentCacheRepository
from domain.models import POSITION_KIND_VISIBLE_TEXT, DocumentBundle, PayloadDocument, Record, Result, Site
from fetching.client import FetchContext
from output.formatter import format_results, multi_failure_text
from output.transaction import write_formal_outputs_and_cache, write_outputs
from parsers.registry import ParserRegistry
from services.multi_period import scrape_site_multi_results
from services.failed_site_validation import validate_failed_sites, validation_summary
from services.multi_period import scrape_sites_for_periods
from services.recent_history import HistoryResult, history_result_from_records, scrape_history_sites
from services.single_period import parse_bundle_for_period, scrape_site, scrape_sites
from diagnostics.run_report import format_failure, progress_summary_line


ROOT = Path(__file__).resolve().parents[1]


def generic_site(name: str = "测试站", *, pick: str = "top", url: str = "https://example.test/site") -> Site:
    return Site(name, pick, url, parser="generic_two_zodiac", payload="page")


def test_parse_bundle_validates_each_document_and_provenance() -> None:
    site = generic_site()
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle(
        (
            PayloadDocument("页面", site.url, "210期绝杀二肖【狗蛇】开:11", record_id="topic:1"),
            PayloadDocument("脚本", site.url + "/a.js", "210期绝杀二肖【狗蛇】开:11", record_id="topic:1"),
        )
    )

    records, selected = parse_bundle_for_period(bundle, site, 210, registry)

    assert selected.zodiac == "狗蛇"
    assert selected.record_id == "topic:1"
    assert records[0].document_url == site.url

    conflict = DocumentBundle(
        (
            bundle.documents[0],
            PayloadDocument("诱饵", site.url + "/b.js", "210期绝杀二肖【龙虎】开:22", record_id="topic:1"),
        )
    )
    with pytest.raises(ValueError, match="多文档近3条结果不同"):
        parse_bundle_for_period(conflict, site, 210, registry)


def test_multi_document_direction_miss_reports_actual_window() -> None:
    site = generic_site(url="https://example.test/topic/1")
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle(
        (
            PayloadDocument(
                "原始页面",
                site.url,
                (
                    "测试站 212期绝杀二肖【龙猴】开:00 "
                    "210期绝杀二肖【龙鼠】开:00 "
                    "209期绝杀二肖【虎牛】开:00"
                ),
                record_id="topic:1",
            ),
            PayloadDocument(
                "无关脚本",
                "https://example.test/a.js",
                "没有候选",
                record_id="topic:1",
            ),
        )
    )

    with pytest.raises(
        ValueError,
        match=r"原始页面：top 候选内未找到 211 期；实际近3条：.*212期龙猴.*210期龙鼠.*209期虎牛",
    ):
        parse_bundle_for_period(bundle, site, 211, registry)


def test_single_site_and_ten_worker_progress_use_full_pipeline() -> None:
    sites = [generic_site("A", url="https://example.test/a"), generic_site("B", url="https://example.test/b")]
    sources = {
        sites[0].url: "210期绝杀二肖【狗蛇】开:11",
        sites[1].url: "210期绝杀二肖【龙虎】开:22",
    }
    context = FetchContext(text_fetcher=lambda url, _timeout: sources[url], renderer=lambda *_args: "")
    registry = ParserRegistry.bind_sites(sites)
    progress: list[str] = []

    one = scrape_site(sites[0], 210, 3, context, registry)
    results = scrape_sites(sites, 210, 3, 10, context=context, registry=registry, progress=progress.append)

    assert one.ok and one.record and one.record.zodiac == "狗蛇"
    assert [result.record.zodiac for result in results if result.record] == ["狗蛇", "龙虎"]
    assert len(progress) == 2
    assert progress[-1].startswith("[进度 2/2 100% 成功 2 失败 0")
    assert "当前：" in progress[-1] and "成功" in progress[-1]


def test_multi_period_reuses_one_payload_and_does_not_require_cache(tmp_path: Path) -> None:
    site = generic_site()
    calls: list[str] = []
    source = "210期绝杀二肖【狗蛇】开:11 209期绝杀二肖【龙虎】开:22"
    context = FetchContext(
        text_fetcher=lambda url, _timeout: calls.append(url) or source,
        renderer=lambda *_args: "",
    )
    registry = ParserRegistry.bind_sites([site])
    cache = tmp_path / "recent_10_cache.json"

    results = scrape_site_multi_results(site, [210, 209], 3, context, registry)

    assert [result.ok for result in results] == [True, True]
    assert [result.record.zodiac for result in results if result.record] == ["狗蛇", "龙虎"]
    assert calls == [site.url]
    assert not cache.exists()


def test_recent_history_keeps_current_and_nine_previous_in_original_positions() -> None:
    site = generic_site(pick="top")
    records = [Record(period, "狗蛇", "", "", index) for index, period in enumerate(range(210, 199, -1))]

    result = history_result_from_records(site, records, 210)

    assert result.ok
    assert [record.period for record in result.records] == list(range(210, 200, -1))
    assert [record.position for record in result.records] == list(range(10))


def test_success_and_failure_txt_format_is_exact() -> None:
    ok_site = generic_site("成功站")
    bad_site = generic_site("失败站", pick="bottom", url="https://example.test/bad")
    results = [
        Result(ok_site, Record(210, "狗蛇", "", "", 0)),
        Result(bad_site, None, "未抓到有效候选"),
    ]

    success, failure = format_results(results, include_url=False, append_fixed_tail=True)

    assert success == (
        "狗蛇 成功站\n黄杀\n有点帅\n金绝\n金元宝\n男人牛\n\n"
        "生肖次数排行榜\n狗 1次\n蛇 1次\n"
    )
    assert failure == "失败站 bottom https://example.test/bad 原因：未抓到有效候选\n"


def test_failure_txt_has_one_blank_line_between_sites(tmp_path: Path) -> None:
    results = [
        Result(generic_site("A"), None, "原因A"),
        Result(generic_site("B", url="https://example.test/b"), None, "原因B"),
    ]
    success = tmp_path / "210期-二肖.txt"
    failure = tmp_path / "210期-二肖-失败.txt"

    write_outputs(results, success, failure, include_url=False, append_fixed_tail=True)

    assert success.read_text(encoding="utf-8") == ""
    assert failure.read_text(encoding="utf-8") == (
        "A top https://example.test/site 原因：原因A\n\n"
        "B top https://example.test/b 原因：原因B\n"
    )


def test_formal_transaction_rolls_back_txt_when_cache_commit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "success.txt"
    errors = tmp_path / "failure.txt"
    cache_path = tmp_path / "cache.json"
    output.write_text("old-success", encoding="utf-8")
    errors.write_text("old-failure", encoding="utf-8")
    cache_path.write_text("old-cache", encoding="utf-8")
    repository = RecentCacheRepository(cache_path)
    monkeypatch.setattr(repository, "commit", lambda _payload: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        write_formal_outputs_and_cache(
            [Result(generic_site(), Record(210, "狗蛇", "", "", 0))],
            output,
            errors,
            repository,
            {"issues": [210], "sites": []},
            include_url=False,
        )

    assert output.read_text(encoding="utf-8") == "old-success"
    assert errors.read_text(encoding="utf-8") == "old-failure"
    assert cache_path.read_text(encoding="utf-8") == "old-cache"


def test_multi_failure_report_only_lists_sites_failing_every_period() -> None:
    a = generic_site("A")
    b = generic_site("B", url="https://example.test/b")
    period_results = {
        210: [Result(a, None, "A210"), Result(b, Record(210, "狗蛇", "", "", 0))],
        209: [Result(a, None, "A209"), Result(b, None, "B209")],
    }

    text = multi_failure_text(period_results)

    assert text == (
        "A top https://example.test/site\n"
        "210期：A210\n209期：A209\n"
    )


def test_cli_period_rules_and_defaults() -> None:
    assert cli.parse_period("210期") == 210
    with pytest.raises(Exception, match="3 位数字"):
        cli.parse_period("21")
    with pytest.raises(Exception, match="001-365"):
        cli.parse_period("999")
    args = cli.parse_args(["--period", "210"])
    assert args.workers == 10 and args.timeout == 25
    with pytest.raises(SystemExit):
        cli.parse_args(["--period", "210", "--periods", "209"])


def test_cli_limit_mode_never_writes_txt_or_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sites_path = tmp_path / "sites.json"
    sites_path.write_text(
        json.dumps([{"name": "A", "pick": "top", "url": "https://example.test/a", "payload": "page"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "scrape_sites",
        lambda sites, *_args, **_kwargs: [Result(sites[0], Record(210, "狗蛇", "", "", 0))],
    )

    code = cli.main(
        ["--period", "210", "--sites-file", str(sites_path), "--limit", "1", "--adaptive-mode", "off"]
    )

    assert code == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["sites.json"]


def test_bat_entries_use_v2_launcher_and_ten_workers() -> None:
    single = (ROOT / "运行_手动输入期数.bat").read_text(encoding="utf-8")
    multi = (ROOT / "运行_手动输入多期.bat").read_text(encoding="utf-8")
    assert 'two_zodiac_site_scraper.py" --period %PERIOD% --workers 10' in single
    assert 'two_zodiac_site_scraper.py" --periods %PERIODS% --workers 10' in multi


def test_bat_entries_require_python_310() -> None:
    version_probe = (
        'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 1)'
    )
    for filename in ("运行_手动输入期数.bat", "运行_手动输入多期.bat"):
        source = (ROOT / filename).read_text(encoding="utf-8")

        assert f'py.exe -3.10 -c "{version_probe}"' in source
        assert 'set "PYTHON_CMD=py.exe -3.10"' in source
        assert f'python.exe -c "{version_probe}"' in source
        assert 'set "PYTHON_CMD=py.exe -3"' not in source
        assert "Cannot find Python 3.10" in source


@pytest.mark.parametrize(
    ("exc", "prefix"),
    [
        (requests.exceptions.Timeout("slow"), "网络请求超时"),
        (requests.exceptions.SSLError("tls"), "SSL连接失败"),
        (requests.RequestException("http"), "HTTP请求失败"),
        (ValueError("浏览器渲染失败"), "浏览器渲染失败"),
        (ValueError("top 候选内未找到 210 期"), "内容尚未发布"),
        (ValueError("数据存在冲突"), "数据存在冲突"),
        (ValueError("未找到专属锚点"), "未找到专属锚点"),
        (ValueError("未抓到有效候选"), "未找到指定目标"),
        (ValueError("未找到目标脚本"), "页面结构已变化"),
        (ValueError("其它字段错误"), "字段校验未通过"),
    ],
)
def test_failure_classification_is_stable(exc: BaseException, prefix: str) -> None:
    assert format_failure(exc).startswith(prefix + "：")
    assert "0%" in progress_summary_line(0, 0, 0, 0, 0.0, "A", False, "失败")


def test_parse_bundle_supports_explicit_linked_title_body_only() -> None:
    site = Site(
        "目标站",
        "top",
        "https://example.test/index",
        parser="site_scoped_two_zodiac",
        title="目标站",
        payload="page_and_scripts",
        linked_document_pattern=r"/target\.js$",
    )
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle(
        (
            PayloadDocument("标题", site.url, '<h1>目标站</h1><script src="/target.js"></script>'),
            PayloadDocument(
                "正文",
                "https://example.test/target.js",
                "210期绝杀二肖【狗蛇】开:11",
                parent_url=site.url,
                link_reference="/target.js",
            ),
        )
    )

    records, selected = parse_bundle_for_period(bundle, site, 210, registry)
    assert selected.zodiac == "狗蛇" and records

    unlinked = DocumentBundle(
        (
            bundle.documents[0],
            PayloadDocument(
                "正文",
                "https://other.test/target.js",
                bundle.documents[1].source,
                parent_url=site.url,
                link_reference="/target.js",
            ),
        )
    )
    with pytest.raises(ValueError, match="文档关系边界无效"):
        parse_bundle_for_period(unlinked, site, 210, registry)


def test_parse_bundle_combines_same_url_title_views_before_linking_body() -> None:
    site = Site(
        "目标站",
        "top",
        "https://example.test/index",
        parser="site_scoped_two_zodiac",
        title="目标站",
        payload="page_and_scripts",
        linked_document_pattern=r"/target\.js$",
    )
    registry = ParserRegistry.bind_sites([site])
    script_url = "https://example.test/list.js"
    bundle = DocumentBundle(
        (
            PayloadDocument("脚本", script_url, "目标站"),
            PayloadDocument("脚本解码", script_url, '<a href="/target.js">目标正文</a>'),
            PayloadDocument(
                "正文",
                "https://example.test/target.js",
                "210期绝杀二肖【狗蛇】开:11",
                parent_url=script_url,
                link_reference="/target.js",
            ),
        )
    )

    _records, selected = parse_bundle_for_period(bundle, site, 210, registry)
    assert selected.zodiac == "狗蛇"


def test_scrape_site_formats_failure_and_failed_validator_never_writes(tmp_path: Path) -> None:
    site = generic_site()
    context = FetchContext(text_fetcher=lambda *_args: "空页面", renderer=lambda *_args: "")
    registry = ParserRegistry.bind_sites([site])

    result = scrape_site(site, 210, 3, context, registry)
    validated = validate_failed_sites([site], 210, 3, 10, context=context, registry=registry, progress=None)

    assert not result.ok and result.error.startswith("未找到指定目标")
    assert not validated[0].ok
    assert "未抓到210期" in validation_summary(validated[0], 210)
    assert list(tmp_path.iterdir()) == []


def test_failed_validator_success_summary() -> None:
    site = generic_site()
    result = Result(site, Record(210, "狗蛇", "", "", 0))
    summary = validation_summary(result, 210)
    assert "实际生肖=狗蛇" in summary and "同期冲突=无" in summary


def test_history_bottom_missing_and_conflict_paths() -> None:
    site = generic_site(pick="bottom")
    records = [Record(period, "狗蛇", "", "", index) for index, period in enumerate(range(201, 211))]
    assert history_result_from_records(site, records, 210).ok

    missing = [record for record in records if record.period != 205]
    missing_result = history_result_from_records(site, missing, 210)
    assert not missing_result.ok and "缺少 205 期数据" in missing_result.error

    conflict = [*records, Record(210, "龙虎", "", "", len(records))]
    conflict_result = history_result_from_records(site, conflict, 210)
    assert not conflict_result.ok


def test_history_merges_same_zodiac_at_multiple_positions() -> None:
    site = generic_site(pick="bottom")
    records = [Record(period, "狗蛇", "", "", index) for index, period in enumerate(range(201, 210))]
    records.extend(
        [
            Record(210, "狗蛇", "", "", len(records)),
            Record(210, "狗蛇", "", "", len(records) + 1),
        ]
    )

    result = history_result_from_records(site, records, 210)

    assert result.ok
    current = next(record for record in result.records if record.period == 210)
    assert current.source_positions == (9, 10)


def test_history_concurrent_pipeline_reports_success() -> None:
    sites = [generic_site("A", url="https://example.test/a"), generic_site("B", url="https://example.test/b")]
    source = " ".join(f"{period}期绝杀二肖【狗蛇】开:11" for period in range(210, 200, -1))
    context = FetchContext(text_fetcher=lambda *_args: source, renderer=lambda *_args: "")
    registry = ParserRegistry.bind_sites(sites)
    progress: list[str] = []

    results = scrape_history_sites(
        sites,
        210,
        3,
        10,
        context=context,
        registry=registry,
        progress=progress.append,
    )

    assert all(result.ok for result in results)
    assert len(progress) == 2 and "进度 2/2" in progress[-1]


def test_period_scoped_multi_fetches_each_period_and_batch_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    site = Site("列表站", "top", "https://example.test/list", parser="generic_two_zodiac", payload="topic_list_detail")
    registry = ParserRegistry.bind_sites([site])
    calls: list[int | None] = []

    def fake_fetch(_site, period, *_args):
        calls.append(period)
        return DocumentBundle((PayloadDocument("详情", f"https://example.test/{period}", f"{period}期绝杀二肖【狗蛇】开:11"),))

    monkeypatch.setattr("services.multi_period.fetch_payload", fake_fetch)
    progress: list[str] = []
    by_period = scrape_sites_for_periods(
        [site],
        [210, 209],
        3,
        10,
        context=FetchContext(text_fetcher=lambda *_args: "", renderer=lambda *_args: ""),
        registry=registry,
        progress=progress.append,
    )

    assert calls == [210, 209]
    assert all(by_period[period][0].ok for period in (210, 209))
    assert "进度 1/1" in progress[0]


def test_shared_multi_failure_is_returned_for_every_period() -> None:
    site = generic_site()
    context = FetchContext(text_fetcher=lambda *_args: (_ for _ in ()).throw(requests.Timeout("slow")), renderer=lambda *_args: "")
    registry = ParserRegistry.bind_sites([site])
    results = scrape_site_multi_results(site, [210, 209], 1, context, registry)
    assert len(results) == 2 and all(not result.ok for result in results)
    assert all(result.error.startswith("网络请求超时") for result in results)


def test_cli_duplicate_check_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sites = [generic_site("A"), generic_site("B", url="https://example.test/B")]
    issues = list(range(210, 200, -1))
    complete = {
        "schema": 2,
        "position_kind": POSITION_KIND_VISIBLE_TEXT,
        "issues": issues,
        "sites": [
            {
                "name": site.name,
                "url": site.url,
                "pick": site.pick,
                "position_kind": POSITION_KIND_VISIBLE_TEXT,
                "values": {str(period): ("狗蛇" if site.name == "A" else "龙虎") for period in issues},
                "positions": {str(period): 0 for period in issues},
            }
            for site in sites
        ],
    }
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(complete, ensure_ascii=False), encoding="utf-8")
    assert cli.run_duplicate_check(sites, path) == 0

    complete["sites"][1]["values"] = complete["sites"][0]["values"]
    path.write_text(json.dumps(complete, ensure_ascii=False), encoding="utf-8")
    assert cli.run_duplicate_check(sites, path) == 2
    assert "连续10期" in capsys.readouterr().out


def test_cli_single_period_ignores_cached_position_conflict_and_updates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = generic_site("A", url="https://example.test/a")
    sites_path = tmp_path / "sites.json"
    sites_path.write_text(
        json.dumps(
            [{"name": site.name, "pick": site.pick, "url": site.url, "parser": site.parser, "payload": site.payload}]
        ),
        encoding="utf-8",
    )
    success = tmp_path / "success.txt"
    failure = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    issues = list(range(210, 200, -1))
    cache.write_text(
        json.dumps(
            {
                "schema": 2,
                "position_kind": POSITION_KIND_VISIBLE_TEXT,
                "window_size": 10,
                "issues": issues,
                "sites": [
                    {
                        "name": site.name,
                        "url": site.url,
                        "pick": site.pick,
                        "position_kind": POSITION_KIND_VISIBLE_TEXT,
                        "values": {str(period): "狗蛇" for period in issues},
                        "positions": {str(period): 0 for period in issues},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "scrape_sites", lambda *_args, **_kwargs: [Result(site, Record(210, "狗蛇", "", "", 1))])

    code = cli.main(
        [
            "--period",
            "210",
            "--sites-file",
            str(sites_path),
            "--output",
            str(success),
            "--errors",
            str(failure),
            "--history-cache-file",
            str(cache),
            "--adaptive-mode",
            "off",
        ]
    )

    assert code == 0
    assert "狗蛇 A" in success.read_text(encoding="utf-8")
    assert not failure.exists()
    updated = json.loads(cache.read_text(encoding="utf-8"))
    assert updated["sites"][0]["positions"]["210"] == 1


def test_cli_single_and_history_modes_write_only_isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = generic_site("A", url="https://example.test/a")
    sites_path = tmp_path / "sites.json"
    sites_path.write_text(
        json.dumps([{"name": site.name, "pick": site.pick, "url": site.url, "parser": site.parser, "payload": site.payload}]),
        encoding="utf-8",
    )
    success = tmp_path / "success.txt"
    failure = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(cli, "scrape_sites", lambda *_args, **_kwargs: [Result(site, Record(210, "狗蛇", "", "", 0))])
    code = cli.main(
        [
            "--period", "210", "--sites-file", str(sites_path), "--output", str(success),
            "--errors", str(failure), "--history-cache-file", str(cache), "--adaptive-mode", "off",
        ]
    )
    assert code == 0 and success.exists() and cache.exists() and not failure.exists()

    history_cache = tmp_path / "history.json"
    history = HistoryResult(site, tuple(Record(period, "狗蛇", "", "", 210 - period) for period in range(210, 200, -1)))
    monkeypatch.setattr(cli, "scrape_history_sites", lambda *_args, **_kwargs: [history])
    code = cli.main(
        [
            "--period", "210", "--sites-file", str(sites_path), "--history-cache",
            "--history-cache-file", str(history_cache), "--adaptive-mode", "off",
        ]
    )
    assert code == 0
    assert json.loads(history_cache.read_text(encoding="utf-8"))["issues"][0] == 210


def test_cli_multi_mode_uses_isolated_paths_and_never_updates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = generic_site("A")
    args = argparse.Namespace(periods=[210, 209], timeout=3, workers=10, include_url=False)
    registry = ParserRegistry.bind_sites([site])
    monkeypatch.setattr(
        cli,
        "scrape_sites_for_periods",
        lambda *_args, **_kwargs: {
            210: [Result(site, Record(210, "狗蛇", "", "", 0))],
            209: [Result(site, None, "missing")],
        },
    )
    monkeypatch.setattr(cli, "success_path", lambda period: tmp_path / f"{period}-ok.txt")
    monkeypatch.setattr(cli, "failure_path", lambda period: tmp_path / f"{period}-fail.txt")
    monkeypatch.setattr(cli, "multi_failure_path", lambda periods: tmp_path / "summary.txt")

    assert cli.run_multi_periods([site], args, registry, None) == 0
    assert not (tmp_path / "recent_10_cache.json").exists()
    assert (tmp_path / "summary.txt").exists()
