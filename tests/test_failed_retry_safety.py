from contextlib import contextmanager
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

import cli
import services.failed_retry as retry
from cache.repository import RecentCacheRepository
from domain.models import Site, Record, Result


def setup_files(tmp_path):
    sites = [Site(name, 'top', f'https://example.test/{name}') for name in ('A', 'B')]
    results = [Result(s, Record(248, '牛马', '', '', 10)) for s in sites]
    success, failure, cache = [tmp_path / n for n in ('success.txt', 'failure.txt', 'cache.json')]
    success.write_bytes(b'original\r\n')
    failure.write_text(
        ''.join(f'{s.name} {s.pick} {s.url} 原因：失败\n' for s in sites),
        encoding='utf-8',
    )
    repo = RecentCacheRepository(cache)
    repo.commit(repo.prepare_update(results, 248))
    return results, success, failure, cache


@pytest.mark.parametrize('pair', [(0, 1), (0, 2), (1, 2)])
def test_colliding_paths_never_write(tmp_path, pair):
    results, *paths = setup_files(tmp_path)
    before = {p: p.read_bytes() for p in paths}
    paths[pair[0]] = paths[pair[1]]
    with pytest.raises(ValueError, match='路径冲突'):
        retry.apply_retry(results, *paths, 248, False)
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize('invalid', ['schema', 'duplicate'])
def test_invalid_cache_keeps_valid_outputs(tmp_path, invalid):
    results, success, failure, cache = setup_files(tmp_path)
    data = json.loads(cache.read_text(encoding='utf-8'))
    if invalid == 'schema':
        data['schema'] = 1
    else:
        data['sites'].append(copy.deepcopy(data['sites'][0]))
    cache.write_text(json.dumps(data), encoding='utf-8')
    before = cache.read_bytes()
    with pytest.raises(RuntimeError, match='已保留'):
        retry.apply_retry(results, success, failure, cache, 248, False)
    assert '牛马 A' in success.read_text(encoding='utf-8')
    assert failure.read_bytes() == b''
    assert cache.read_bytes() == before


@pytest.mark.parametrize('bad', ['encoding', 'permission'])
def test_cli_failure_read_is_controlled(tmp_path, monkeypatch, capsys, bad):
    results, success, failure, cache = setup_files(tmp_path)
    monkeypatch.setattr(cli, 'load_sites', lambda *a, **k: [r.site for r in results])
    monkeypatch.setattr(cli.ParserRegistry, 'bind_sites', lambda *a: None)

    def no_fetch(*a, **k):
        pytest.fail('must not fetch')

    monkeypatch.setattr(cli, 'scrape_sites', no_fetch)
    if bad == 'encoding':
        failure.write_bytes(b'\xff')
    else:
        def denied(*a):
            raise PermissionError('denied')

        monkeypatch.setattr(cli, 'sites_from_failure_file', denied)
    assert cli.main([
        '--period', '248', '--retry-failures', '--errors', str(failure),
        '--output', str(success), '--history-cache-file', str(cache),
    ]) == 2
    output = capsys.readouterr().out
    assert '失败TXT读取失败，未执行重抓' in output
    assert 'Traceback' not in output


def test_concurrent_retry_serializes_output_without_losing_success(tmp_path, monkeypatch):
    results, success, failure, cache = setup_files(tmp_path)
    entered, release, second_attempt = Event(), Event(), Event()
    append = retry.append_repaired_successes
    real_output_lock = retry.output_lock

    def paused(items, *a, **k):
        if items and items[0].site.name == 'A':
            entered.set()
            assert release.wait(5)
        return append(items, *a, **k)

    @contextmanager
    def observed_output_lock(*paths, **kwargs):
        if entered.is_set():
            second_attempt.set()
        with real_output_lock(*paths, **kwargs):
            yield

    monkeypatch.setattr(retry, 'append_repaired_successes', paused)
    monkeypatch.setattr(retry, 'output_lock', observed_output_lock)
    monkeypatch.setattr(retry, 'update_current_cache', lambda *a, **k: None)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            retry.apply_retry, results[:1], success, failure, cache, 248, False
        )
        assert entered.wait(5)
        second = pool.submit(
            retry.apply_retry, results[1:], success, failure, cache, 248, False
        )
        assert second_attempt.wait(5)
        assert not second.done()
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)

    text = success.read_text(encoding='utf-8')
    assert text.count('牛马 A') == 1
    assert text.count('牛马 B') == 1
    assert failure.read_bytes() == b''


def test_stale_cache_retry_cannot_overwrite_newer_retry(tmp_path):
    results, _success, _failure, cache = setup_files(tmp_path)
    first = Result(results[0].site, Record(248, '虎兔', '', '', 11))
    second = Result(results[1].site, Record(248, '蛇鸡', '', '', 12))
    first_repo = RecentCacheRepository(cache)
    second_repo = RecentCacheRepository(cache)

    prepared_first = first_repo.prepare_current_period_update(248, [first])
    prepared_second = second_repo.prepare_current_period_update(248, [second])
    first_repo.commit(prepared_first)

    with pytest.raises(ValueError, match='prepare后发生变化'):
        second_repo.commit(prepared_second)

    second_repo.update_current_period(248, [second])
    payload = first_repo.read()
    by_name = {entry['name']: entry for entry in payload['sites']}
    assert by_name['A']['values']['248'] == '虎兔'
    assert by_name['A']['positions']['248'] == 11
    assert by_name['B']['values']['248'] == '蛇鸡'
    assert by_name['B']['positions']['248'] == 12
