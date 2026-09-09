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
    failure.write_text(''.join(f'{s.name} {s.pick} {s.url} 原因：失败\n' for s in sites), encoding='utf-8')
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
    assert cli.main(['--period', '248', '--retry-failures', '--errors', str(failure), '--output', str(success), '--history-cache-file', str(cache)]) == 2
    assert '失败' in capsys.readouterr().out


def test_concurrent_retry_cannot_lose_success(tmp_path, monkeypatch):
    results, success, failure, cache = setup_files(tmp_path)
    entered, release = Event(), Event()
    append = retry.append_repaired_successes
    def paused(items, *a, **k):
        if items and items[0].site.name == 'A':
            entered.set()
            assert release.wait(5)
        return append(items, *a, **k)
    monkeypatch.setattr(retry, 'append_repaired_successes', paused)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(retry.apply_retry, results[:1], success, failure, cache, 248, False)
        assert entered.wait(5)
        try:
            with pytest.raises(ValueError):
                retry.apply_retry(results[1:], success, failure, cache, 248, False)
        finally:
            release.set()
        first.result(timeout=5)
    retry.apply_retry(results[1:], success, failure, cache, 248, False)
    assert success.read_text(encoding='utf-8').count('牛马 A') == 1
    assert success.read_text(encoding='utf-8').count('牛马 B') == 1
    assert failure.read_bytes() == b''
