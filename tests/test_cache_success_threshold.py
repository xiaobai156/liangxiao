from __future__ import annotations

import json
from pathlib import Path

import cli
from domain.models import Record, Result, Site


def make_site(index: int) -> Site:
    return Site(
        f"站点{index}",
        "top",
        f"https://example.test/site-{index}",
        parser="named_block",
        payload="page",
    )


def test_cache_update_requires_strictly_more_than_85_percent() -> None:
    assert not cli.should_update_cache(17, 20)
    assert cli.should_update_cache(18, 20)
    assert not cli.should_update_cache(0, 0)


def test_single_period_keeps_cache_when_success_rate_is_exactly_85_percent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sites = [make_site(index) for index in range(20)]
    sites_file = tmp_path / "sites.json"
    sites_file.write_text(
        json.dumps(
            [
                {
                    "name": site.name,
                    "pick": site.pick,
                    "url": site.url,
                    "parser": site.parser,
                    "payload": site.payload,
                }
                for site in sites
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    success = tmp_path / "success.txt"
    failure = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    old_cache = b"cache-is-unchanged"
    cache.write_bytes(old_cache)
    results = [
        Result(site, Record(210, "狗蛇", "", "", index))
        if index < 17
        else Result(site, None, "测试失败")
        for index, site in enumerate(sites)
    ]
    monkeypatch.setattr(cli, "scrape_sites", lambda *_args, **_kwargs: results)

    code = cli.main(
        [
            "--period",
            "210",
            "--sites-file",
            str(sites_file),
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

    assert code == 1
    assert cache.read_bytes() == old_cache
    assert success.exists()
    assert failure.exists()


def test_single_period_marks_failed_sites_when_cache_is_allowed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sites = [make_site(index) for index in range(20)]
    sites_file = tmp_path / "sites.json"
    sites_file.write_text(
        json.dumps(
            [
                {
                    "name": site.name,
                    "pick": site.pick,
                    "url": site.url,
                    "parser": site.parser,
                    "payload": site.payload,
                }
                for site in sites
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    success = tmp_path / "success.txt"
    failure = tmp_path / "failure.txt"
    cache = tmp_path / "cache.json"
    results = [
        Result(site, Record(210, "狗蛇", "", "", index))
        if index < 18
        else Result(site, None, "测试失败")
        for index, site in enumerate(sites)
    ]
    monkeypatch.setattr(cli, "scrape_sites", lambda *_args, **_kwargs: results)

    code = cli.main(
        [
            "--period",
            "210",
            "--sites-file",
            str(sites_file),
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

    assert code == 1
    payload = json.loads(cache.read_text(encoding="utf-8"))
    failed = {entry["name"]: entry for entry in payload["sites"] if entry.get("status") == "failed"}
    assert set(failed) == {"站点18", "站点19"}
    assert all(entry["error"] == "测试失败" for entry in failed.values())

