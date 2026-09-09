from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from cache.contracts import validate_cache_position_contract
from cache.repository import RecentCacheRepository
from domain.models import HistoryResult, Record, Result, Site
from output import transaction
from output.formatter import format_results
from output.locking import output_lock
from services import failed_retry


def result(name="站点", period=100, zodiac="虎兔", position=7, *, payload="page"):
    return Result(Site(name, "top", f"https://example.test/{name}", payload=payload),
                  Record(period, zodiac, "00", f"{period}期 {zodiac}", position))


def seed(path: Path, results: list[Result]):
    repo = RecentCacheRepository(path)
    repo.commit(repo.prepare_history_update([
        HistoryResult(item.site, (item.record, replace(item.record, period=99)))
        for item in results
    ], 100))
    return repo


def test_output_snapshot_and_rollback_remain_inside_lock(tmp_path, monkeypatch):
    success, failure = tmp_path / "success.txt", tmp_path / "failure.txt"
    success.write_bytes(b"old success")
    failure.write_bytes(b"old failure")
    held = False
    original_snapshot, original_restore = transaction._snapshot, transaction._restore

    @contextmanager
    def guarded_lock(*paths):
        nonlocal held
        with output_lock(*paths):
            held = True
            try:
                yield
            finally:
                held = False

    def snapshot(paths):
        assert held, "snapshot outside transaction lock"
        return original_snapshot(paths)

    def restore(state):
        assert held, "rollback outside transaction lock"
        original_restore(state)

    write = transaction.atomic_write_text
    def fail_second(path, text):
        if path == failure:
            raise OSError("second output failed")
        write(path, text)

    monkeypatch.setattr(transaction, "output_lock", guarded_lock)
    monkeypatch.setattr(transaction, "_snapshot", snapshot)
    monkeypatch.setattr(transaction, "_restore", restore)
    monkeypatch.setattr(transaction, "atomic_write_text", fail_second)
    with pytest.raises(OSError, match="second output"):
        transaction.write_outputs([result(), Result(result("坏站").site, error="失败")],
                                  success, failure, include_url=False)
    assert success.read_bytes() == b"old success"
    assert failure.read_bytes() == b"old failure"
    assert not held


def test_waiting_writer_rollback_preserves_commit_made_before_it_got_lock(tmp_path, monkeypatch):
    success, failure = tmp_path / "success.txt", tmp_path / "failure.txt"
    success.write_bytes(b"version A")
    failure.write_bytes(b"failure A")

    @contextmanager
    def intervening_writer(*paths):
        with output_lock(*paths):
            # Another writer completed while this caller waited for the lock.
            success.write_bytes(b"version B")
            failure.write_bytes(b"failure B")
            yield

    monkeypatch.setattr(transaction, "output_lock", intervening_writer)
    write = transaction.atomic_write_text
    def fail_second(path, text):
        if path == failure:
            raise OSError("injected write failure")
        write(path, text)
    monkeypatch.setattr(transaction, "atomic_write_text", fail_second)
    with pytest.raises(OSError):
        transaction.write_outputs([result(), Result(result("坏站").site, error="失败")],
                                  success, failure, include_url=False)
    assert success.read_bytes() == b"version B"
    assert failure.read_bytes() == b"failure B"


def test_outputs_create_new_directories(tmp_path):
    transaction.write_outputs([result()], tmp_path / "new" / "ok.txt",
                              tmp_path / "errors" / "fail.txt", include_url=False)
    assert (tmp_path / "new" / "ok.txt").exists()


def test_output_lock_serializes_threads_and_has_bounded_wait(tmp_path):
    started, release = Event(), Event()
    failures = []
    def owner():
        try:
            with output_lock(tmp_path / "ok.txt"):
                started.set()
                assert release.wait(3)
        except BaseException as exc:
            failures.append(exc)
    thread = Thread(target=owner)
    thread.start()
    try:
        assert started.wait(3)
        with pytest.raises(TimeoutError):
            with output_lock(tmp_path / "another.txt", timeout=0.02):
                pytest.fail("same directory was not locked")
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not failures


def test_retry_uses_cache_commit_lock_after_releasing_output_lock(tmp_path, monkeypatch):
    good = result()
    cache = tmp_path / "cache.json"
    seed(cache, [good])
    failure = tmp_path / "failure.txt"
    failure.write_text(f"{good.site.name} top {good.site.url} 原因：旧错误\n", encoding="utf-8")
    output_held = False
    cache_held = False
    calls = []
    cache_lock = RecentCacheRepository._lock
    current_hash = RecentCacheRepository._current_hash

    @contextmanager
    def checked_output(*paths):
        nonlocal output_held
        with output_lock(*paths):
            output_held = True
            try:
                yield
            finally:
                output_held = False

    @contextmanager
    def checked_cache(repo):
        nonlocal cache_held
        assert not output_held
        with cache_lock(repo):
            cache_held = True
            calls.append("cache lock")
            try:
                yield
            finally:
                cache_held = False

    def checked_hash(repo):
        assert cache_held
        calls.append("compare")
        return current_hash(repo)

    monkeypatch.setattr(failed_retry, "output_lock", checked_output)
    monkeypatch.setattr(RecentCacheRepository, "_lock", checked_cache)
    monkeypatch.setattr(RecentCacheRepository, "_current_hash", checked_hash)
    failed_retry.apply_retry([good], tmp_path / "success.txt", failure, cache, 100, False)
    assert calls == ["cache lock", "compare"]
    assert failure.read_bytes() == b""


def test_stale_retry_cannot_overwrite_normal_commit(tmp_path):
    first, second = result("甲"), result("乙")
    repo = seed(tmp_path / "cache.json", [first, second])
    partial = repo.prepare_current_period_update(100, [result("甲", zodiac="牛马")])
    repo.commit(repo.prepare_update([first, result("乙", zodiac="龙蛇")], 100))
    committed = repo.path.read_bytes()
    with pytest.raises(ValueError, match="prepare后发生变化"):
        repo.commit(partial)
    assert repo.path.read_bytes() == committed


def test_retry_preserves_other_sites_issues_and_record_metadata(tmp_path):
    first, second = result("甲"), result("乙")
    repo = seed(tmp_path / "cache.json", [first, second])
    before = repo.read()
    before["sites"][0]["records"][1]["custom_evidence"] = "must survive"
    before["sites"][0]["records"][1]["source_positions"] = [7, 17]
    repo.path.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    updated_record = replace(first.record, zodiac="牛马", source_positions=(7, 19))
    repo.update_current_period(100, [replace(first, record=updated_record)])
    after = repo.read()
    validate_cache_position_contract(after)
    assert after["issues"] == before["issues"]
    assert after["sites"][1] == before["sites"][1]
    assert after["sites"][0]["records"][1] == before["sites"][0]["records"][1]
    assert after["sites"][0]["records"][0]["source_positions"] == [7, 19]
    assert after["sites"][0]["fingerprint"] == "牛马虎兔"


@pytest.mark.parametrize("payload", ["page", "admin_article_api"])
def test_retry_removes_stale_article_id_when_record_has_none(tmp_path, payload):
    good = result(payload=payload)
    repo = seed(tmp_path / "cache.json", [good])
    before = repo.read()
    before["sites"][0]["article_ids"] = {"100": "stale", "99": "other-issue"}
    repo.path.write_text(json.dumps(before), encoding="utf-8")
    repo.update_current_period(100, [good])
    assert repo.read()["sites"][0]["article_ids"] == {"99": "other-issue"}


def test_non_article_retry_does_not_store_user_id_as_article(tmp_path):
    good = result(payload="tuku_user_forums")
    repo = seed(tmp_path / "cache.json", [good])
    repo.update_current_period(100, [replace(good, record=replace(good.record, record_id="user:123"))])
    assert "article_ids" not in repo.read()["sites"][0]


@pytest.mark.parametrize("mutate", [
    lambda r: replace(r, period=99),
    lambda r: replace(r, zodiac="牛牛"),
    lambda r: replace(r, position=True),
    lambda r: replace(r, position=-1),
    lambda r: replace(r, position_kind="candidate_index_v1"),
])
def test_invalid_retry_does_not_mutate_cache(tmp_path, mutate):
    good = result()
    repo = seed(tmp_path / "cache.json", [good])
    before = repo.path.read_bytes()
    with pytest.raises(ValueError):
        repo.update_current_period(100, [replace(good, record=mutate(good.record))])
    assert repo.path.read_bytes() == before


def test_retry_cache_failure_keeps_output_and_original_ranking(tmp_path):
    good = result()
    success, failure = tmp_path / "ok.txt", tmp_path / "bad.txt"
    old = "牛马 原站\r\n\r\n内容\t次数\t排名\r\n牛\t1\t1\r\n马\t1\t1\r\n".encode()
    success.write_bytes(old)
    failure.write_text(f"站点 top {good.site.url} 原因：失败\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="成功TXT已保留"):
        failed_retry.apply_retry([good], success, failure, tmp_path / "missing.json", 100, False)
    assert success.read_bytes().replace("虎兔 站点\r\n".encode(), b"") == old
    assert failure.read_bytes() == b""


def test_current_cache_prepare_rejects_changed_configuration(tmp_path):
    good = result()
    repo = seed(tmp_path / "cache.json", [good])
    before = repo.path.read_bytes()
    with pytest.raises(ValueError, match="config_fingerprint"):
        repo.update_current_period(100, [good], sites=[replace(good.site, title="changed")])
    assert repo.path.read_bytes() == before


def test_full_update_preserves_source_positions(tmp_path):
    good = result()
    repo = seed(tmp_path / "cache.json", [good])
    live = replace(good, record=replace(good.record, source_positions=(7, 30)))
    repo.commit(repo.prepare_update([live], 100))
    assert repo.read()["sites"][0]["records"][0]["source_positions"] == [7, 30]


def test_output_normalizes_without_reordering_or_counting_separators():
    text, failure = format_results([result(zodiac="兔，虎")], include_url=False)
    assert text.startswith("兔虎 站点\n")
    assert "虎\t1\t1" in text and "兔\t1\t1" in text
    assert "，" not in text
    assert failure == ""
