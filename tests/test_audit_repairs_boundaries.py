from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import cli
from cache.contracts import config_fingerprint
from cache.serialization import serialize_cached_record
from config.loader import ConfigError, load_sites, site_from_mapping
from domain.errors import ScrapeFailure
from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, HistoryResult, PayloadDocument, Record, Result, Site
from domain.periods import next_period, previous_period
from fetching.client import FetchContext
from fetching.page import canonical_detail_url
from parsers.helpers import html_to_text, select_target_block
from parsers.registry import ParserRegistry, _hydrate_records
from services.multi_period import scrape_site_multi_results
from services.single_period import fetch_topic_list_detail_for_period, parse_bundle_for_period
from validation.boundaries import expected_record_id, validate_document_relationships


def scoped_site(**kwargs):
    return replace(Site('测试站', 'top', 'https://example.test/list',
                        parser='site_scoped_two_zodiac', title='测试站',
                        payload='topic_list_detail'), **kwargs)


def row(period, zodiac='虎兔'):
    return f'<p>{period:03d}期绝杀二肖【{zodiac}】开:00准</p>'


def listing_context(site, entries):
    listing = ''.join(f'<a href="{url}">{period}期 测试站 绝杀二肖</a>'
                      for period, url in entries)
    sources = {url: '<h2>测试站</h2>' + row(int(period))
               for period, url in entries if period.isdigit()}
    sources[site.url] = listing
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return sources[url]
    return FetchContext(text_fetcher=fetch), calls


@pytest.mark.parametrize('pick', ['top', 'bottom'])
def test_list_repeated_periods_occupy_real_window_slots(pick):
    site = scoped_site(pick=pick)
    entries = [('210', 'https://example.test/topic/a.html'),
               ('210', 'https://example.test/topic/b.html'),
               ('209', 'https://example.test/topic/c.html'),
               ('208', 'https://example.test/topic/d.html')]
    if pick == 'bottom':
        entries.reverse()
    context, calls = listing_context(site, entries)
    with pytest.raises(ValueError, match='候选内未找到'):
        fetch_topic_list_detail_for_period(site, 208, 3, context)
    assert calls == [site.url]


def test_invalid_periods_and_exact_repeat_urls_do_not_occupy_list_slots():
    site = scoped_site()
    entries = [('000', 'https://example.test/topic/zero.html'),
               ('999', 'https://example.test/topic/invalid.html'),
               ('1210', 'https://example.test/topic/long.html'),
               ('210', 'https://example.test/topic/a.html'),
               ('210', 'https://example.test/topic/a.html'),
               ('209', 'https://example.test/topic/b.html'),
               ('208', 'https://example.test/topic/c.html')]
    context, calls = listing_context(site, entries)
    bundle = fetch_topic_list_detail_for_period(site, 208, 3, context)
    assert calls == [site.url, 'https://example.test/topic/c.html']
    assert parse_bundle_for_period(bundle, site, 208, ParserRegistry.bind_sites([site]))[1].period == 208


def test_multiple_target_details_inside_window_conflict_instead_of_picking_one():
    site = scoped_site()
    entries = [('210', 'https://example.test/topic/a.html'),
               ('210', 'https://example.test/topic/b.html')]
    context, calls = listing_context(site, entries)
    bundle = fetch_topic_list_detail_for_period(site, 210, 3, context)
    assert len(calls) == 3
    with pytest.raises(ValueError, match='不同记录身份'):
        parse_bundle_for_period(bundle, site, 210, ParserRegistry.bind_sites([site]))


@pytest.mark.parametrize('path', [
    '/view.php?id=123', '/read.php?tid=456', '/topic.php?id=18258',
    '/article.asp?ListId=247&id=53523', '/list.aspx?id=79&page=1',
    '/view.php?id=%E4%B8%AD%E6%96%87&field=a%2Bb',
])
def test_canonical_urls_preserve_all_query_identity(path):
    url = 'https://example.test' + path
    assert canonical_detail_url(url + '#navigation') == url


@pytest.mark.parametrize('source', [
    '<h2>测试站</h2>' + row(210) + '<h2>测试站</h2>' + row(209),
    '<p>210期 测试站 绝杀二肖【虎兔】开:00准</p>'
    '<p>209期 测试站 绝杀二肖【牛马】开:00准</p>',
    ('<h2>测试站</h2>' + row(210)) * 2,
])
def test_ambiguous_target_blocks_fail_closed(source):
    with pytest.raises(ValueError, match='不唯一'):
        select_target_block(source, scoped_site())


def test_configured_stop_is_mandatory_and_does_not_expand_to_next_column():
    site = scoped_site(stop='明确结束')
    with pytest.raises(ValueError, match='停止边界缺失'):
        select_target_block('<h2>测试站</h2>' + row(210) + '<h2>其他栏目</h2>' + row(209), site)
    source = '<h2>测试站</h2>' + row(210) + '<p>明确结束</p>' + row(209)
    block = select_target_block(source, site)
    assert '210期' in block.text and '209期' not in block.text


def test_document_write_html_has_consistent_structured_and_flat_offsets():
    source = 'document.write("<h2>测试站</h2>' + row(210) + '");'
    site = scoped_site(payload='page')
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert len(records) == 1
    assert records[0].zodiac == '虎兔'
    assert records[0].position == html_to_text(source).index('210期')


def test_hydration_rejects_unproven_raw_instead_of_matching_period_and_zodiac():
    site = scoped_site()
    candidate = Record(210, '虎兔', '00', 'this raw never existed', position=0)
    with pytest.raises(ValueError, match='无法定位'):
        _hydrate_records([candidate], '<h2>测试站</h2>' + row(210), site, None)


def test_hydration_does_not_choose_first_of_ambiguous_exact_copies():
    candidate = Record(210, '虎兔', '00', '210期虎兔', position=999)
    with pytest.raises(ValueError, match='原始位置不唯一'):
        _hydrate_records([candidate], '正文 210期虎兔 其他 210期虎兔', scoped_site(), None)


def test_paired_hydration_uses_body_local_position_scope():
    site = scoped_site(payload='page_and_scripts')
    anchor = '<h2>测试站</h2><p>标题只负责证明目标</p>'
    body = row(210) + row(209, '牛马')
    source = anchor + DOCUMENT_BOUNDARY + body
    document = PayloadDocument(
        '标题+正文',
        'https://example.test/body.js',
        source,
        body_source_start=len(anchor) + len(DOCUMENT_BOUNDARY),
        parent_url='https://example.test/list',
        link_reference='https://example.test/body.js',
    )
    raw = '210期绝杀二肖【虎兔】开:00准'
    candidate = Record(210, '虎兔', '00', raw, position=500, anchor_text='测试站')

    records = _hydrate_records([candidate], source, site, document)

    assert len(records) == 1
    assert records[0].position == html_to_text(body).index('210期')
    assert records[0].source_positions == (records[0].position,)
    assert records[0].anchor_text == '测试站'

def test_user_forum_hydration_scopes_positions_to_primary_snapshot_content():
    site = Site(
        '繁忙棒球',
        'bottom',
        'https://example.test/#/users/3978',
        parser='tuku_user_forums_two_zodiac',
        payload='tuku_user_forums',
        title='绝杀二肖',
    )
    content = (
        '193期 绝杀二肖【虎兔】开:00准\n'
        '194期 绝杀二肖【牛马】开:00准\n'
        '195期 绝杀二肖【鸡狗】开:00准'
    )
    items = [
        {'draw': 252, 'topic': '绝杀二肖', 'content': content},
        {'draw': 252, 'topic': '绝杀二肖', 'content': content},
    ]

    records = ParserRegistry.bind_sites([site]).parse(json.dumps(items, ensure_ascii=False), site)

    assert [record.period for record in records] == [193, 194, 195]
    assert [record.position for record in records] == [
        html_to_text(content).index('193期'),
        html_to_text(content).index('194期'),
        html_to_text(content).index('195期'),
    ]
    assert {record.record_path for record in records} == {'root[0].content'}


def test_user_forum_snapshot_scope_preserves_duplicate_rows_as_distinct_positions():
    site = Site(
        '繁忙棒球',
        'bottom',
        'https://example.test/#/users/3978',
        parser='tuku_user_forums_two_zodiac',
        payload='tuku_user_forums',
        title='绝杀二肖',
    )
    row_text = '193期 绝杀二肖【虎兔】开:00准'
    content = row_text + '\n' + row_text
    items = [{'draw': 252, 'topic': '绝杀二肖', 'content': content}]

    records = ParserRegistry.bind_sites([site]).parse(json.dumps(items, ensure_ascii=False), site)

    assert len(records) == 2
    assert records[0].position != records[1].position
    assert [record.position for record in records] == [
        match.start() for match in __import__('re').finditer(__import__('re').escape(row_text), html_to_text(content))
    ]

def test_agreeing_documents_retain_all_provenance_and_stable_carrier():
    site = scoped_site(payload='page_and_scripts', url='https://example.test/page')
    docs = (PayloadDocument('B', 'https://example.test/b.js', '<h2>测试站</h2>' + row(210)),
            PayloadDocument('A', 'https://example.test/a.js', '<p>前缀</p><h2>测试站</h2>' + row(210)))
    registry = ParserRegistry.bind_sites([site])
    first = parse_bundle_for_period(DocumentBundle(docs), site, 210, registry)[1]
    second = parse_bundle_for_period(DocumentBundle(tuple(reversed(docs))), site, 210, registry)[1]
    assert first.document_url == second.document_url == 'https://example.test/a.js'
    assert first.evidence == second.evidence
    assert len(first.evidence) == 2
    assert {e.document_url for e in first.evidence} == {doc.url for doc in docs}
    assert {e.window_index for e in first.evidence} == {0}
    assert len({e.position for e in first.evidence}) == 2
    assert len(serialize_cached_record(site, first)['evidence']) == 2



def test_paired_hydration_skips_anchor_only_candidates():
    site = scoped_site(parser='named_block', payload='page_and_scripts', title='目标标题')
    anchor = '目标标题\n252期 虎兔 开00'
    body = '252期 龙蛇 开00\n251期 马羊 开01'
    source = anchor + DOCUMENT_BOUNDARY + body
    document = PayloadDocument(
        '标题+正文', 'https://example.test/body.js', source,
        body_source_start=len(anchor) + len(DOCUMENT_BOUNDARY),
        parent_url='https://example.test/page', link_reference='body.js',
    )
    records = [
        Record(252, '虎兔', '00', '252期 虎兔 开00', anchor.index('252期'), block_start=0, block_end=len(anchor)),
        Record(252, '龙蛇', '00', '252期 龙蛇 开00', 500, block_start=0, block_end=len(body)),
    ]
    hydrated = _hydrate_records(records, source, site, document)
    assert [(item.period, item.zodiac) for item in hydrated] == [(252, '龙蛇')]
    assert hydrated[0].position == body.index('252期 龙蛇 开00')



def test_paired_hydration_ignores_empty_helper_body():
    site = scoped_site(parser='named_block', payload='page_and_scripts', title='目标标题')
    anchor = '目标标题\n252期 虎兔 开00'
    source = anchor + DOCUMENT_BOUNDARY + '<script></script>'
    document = PayloadDocument(
        '标题+空辅助', 'https://example.test/helper.js', source,
        body_source_start=len(anchor) + len(DOCUMENT_BOUNDARY),
        parent_url='https://example.test/page', link_reference='helper.js',
    )
    records = [Record(252, '虎兔', '00', '252期 虎兔 开00', anchor.index('252期'))]
    assert _hydrate_records(records, source, site, document) == []

def test_user_forum_hydration_fails_closed_on_invalid_snapshot_path():
    site = scoped_site(payload='tuku_user_forums')
    source = json.dumps([{'content': '252期杀虎兔'}], ensure_ascii=False)
    with pytest.raises(ScrapeFailure, match='论坛快照正文边界无效'):
        _hydrate_records(
            [Record(252, '虎兔', '00', '252期杀虎兔', 0, record_path='root[9].content')],
            source, site, PayloadDocument('接口', site.url, source),
        )

def test_url_id_cannot_be_hidden_by_assigned_parent_or_own_id():
    doc = PayloadDocument('错误文章', 'https://example.test/topic/wrong.html', '正文',
                          record_id='topic:target', own_record_id='topic:target')
    with pytest.raises(ValueError, match='自身记录边界冲突'):
        validate_document_relationships(DocumentBundle((doc,)))


def test_inherited_identity_requires_real_parent_link():
    doc = PayloadDocument('脚本', 'https://example.test/body.js', '正文',
                          record_id='topic:target', identity_inherited=True)
    with pytest.raises(ValueError, match='继承身份'):
        validate_document_relationships(DocumentBundle((doc,)))


@pytest.mark.parametrize('url', ['https://example.test/#/users/12', 'https://example.test/users/12'])
def test_user_id_boundary_accepts_path_and_fragment_forms(url):
    assert expected_record_id(scoped_site(url=url, payload='tuku_user_forums')) == 'user:12'


def xiaosuan_item(period, zodiac):
    return {'id': period, 'draw': period, 'topic': '杀肖', 'status': 'published',
            'user_id': 214200, 'user': {'id': 214200, 'nickname': '小算算'},
            'content': f'{period}期杀{zodiac}'}


@pytest.mark.parametrize('missing', [False, True])
def test_xiaosuan_multi_period_uses_each_manual_period_and_shares_only_raw_api(missing):
    site = Site('小算算', 'bottom',
                'https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200',
                parser='xiaosuan_bottom_two_zodiac', payload='tuku_user_forums')
    items = [xiaosuan_item(236, '马鸡')]
    if not missing:
        items.append(xiaosuan_item(237, '蛇鸡'))
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return json.dumps(items, ensure_ascii=False)
    results = scrape_site_multi_results(site, [236, 237], 3, FetchContext(text_fetcher=fetch),
                                       ParserRegistry.bind_sites([site]))
    assert results[0].ok and results[0].record.zodiac == '马鸡'
    assert results[1].ok is not missing
    if not missing:
        assert results[1].record.zodiac == '蛇鸡'
    assert len(calls) == 1


def mapping(**overrides):
    return dict(name='测试站', url='https://example.test/view.php?id=1&a=2',
                pick='top', parser='site_scoped_two_zodiac', payload='page', title='目标栏', **overrides)


def test_unknown_configuration_key_fails_before_scraping():
    with pytest.raises(ConfigError, match='未知字段'):
        site_from_mapping(mapping(paylod='page'))


@pytest.mark.parametrize('origin', ['https://*.example.test', 'https://example.test/path',
                                  'https://name:password@example.test', 'ftp://example.test'])
def test_origin_allowlists_are_explicit_origins_not_patterns(origin):
    with pytest.raises(ConfigError, match='来源|HTTP'):
        site_from_mapping(mapping(allowed_document_origins=[origin]))


def test_same_source_with_display_alias_and_query_reordering_is_rejected(tmp_path):
    items = [mapping(), {**mapping(), 'name': '换名字', 'url': 'https://example.test/view.php?a=2&id=1'}]
    path = tmp_path / 'sites.json'
    path.write_text(json.dumps(items), encoding='utf-8')
    with pytest.raises(ConfigError, match='来源身份重复'):
        load_sites(path)


def test_distinct_fields_on_one_page_are_not_falsely_deduplicated(tmp_path):
    items = [mapping(), {**mapping(), 'name': '不同栏目', 'title': '不同栏目'}]
    path = tmp_path / 'sites.json'
    path.write_text(json.dumps(items), encoding='utf-8')
    assert len(load_sites(path)) == 2


@pytest.mark.parametrize('mutation', [{'title': ''}, {'record': ''}])
def test_named_block_required_fields_are_checked_on_full_config_load(tmp_path, mutation):
    item = {**mapping(), 'parser': 'named_block', 'record': r'(?P<period>\d{3})期(?P<zodiac>虎兔)(?P<open>00)', **mutation}
    path = tmp_path / 'sites.json'
    path.write_text(json.dumps([item]), encoding='utf-8')
    with pytest.raises(ConfigError, match='title和record'):
        load_sites(path)


def test_record_pattern_requires_all_named_groups():
    item = {**mapping(), 'parser': 'named_block', 'record': r'(?P<period>\d{3})期'}
    with pytest.raises(ConfigError, match='命名组'):
        site_from_mapping(item)


def test_default_config_fingerprint_remains_compatible_when_policy_is_empty():
    site = scoped_site()
    assert config_fingerprint([site]) == config_fingerprint([
        replace(site, allowed_document_origins=(), allowed_redirect_origins=())])
    assert config_fingerprint([site]) != config_fingerprint([
        replace(site, allowed_document_origins=('https://cdn.test',))])


def test_all_existing_configuration_is_still_loadable():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / 'config/sites.json').read_text(encoding='utf-8-sig'))
    assert len(load_sites(root / 'config/sites.json')) == len(raw)


def test_multi_period_exit_status_reports_partial_failure(tmp_path, monkeypatch):
    site = scoped_site()
    args = cli.parse_args(['--periods', '209', '210'])
    monkeypatch.setattr(cli, 'scrape_sites_for_periods', lambda *a, **kw: {
        209: [Result(site, Record(209, '虎兔', '00', 'raw', 0))],
        210: [Result(site, error='目标缺失')]})
    monkeypatch.setattr(cli, 'success_path', lambda period: tmp_path / f'{period}-ok.txt')
    monkeypatch.setattr(cli, 'failure_path', lambda period: tmp_path / f'{period}-fail.txt')
    monkeypatch.setattr(cli, 'multi_failure_path', lambda periods: tmp_path / 'all-failed.txt')
    assert cli.run_multi_periods([site], args, ParserRegistry.bind_sites([site])) == 1


def test_history_exit_status_reports_partial_failure(tmp_path, monkeypatch):
    sites = [scoped_site(), scoped_site(name='另站', url='https://example.test/other')]
    monkeypatch.setattr(cli, 'load_sites', lambda *a, **kw: sites)
    monkeypatch.setattr(cli, 'scrape_history_sites', lambda *a, **kw: [
        HistoryResult(sites[0], (Record(210, '虎兔', '00', 'raw', 0),)),
        HistoryResult(sites[1], (), '未找到目标')])
    assert cli.main(['--period', '210', '--history-cache', '--history-cache-file',
                     str(tmp_path / 'history.json')]) == 1


def test_retry_and_history_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(['--period', '210', '--retry-failures', '--history-cache'])
    assert exc.value.code == 2


@pytest.mark.parametrize('period', [1, 2, 100, 365])
def test_period_helpers_wrap_without_changing_order(period):
    assert next_period(previous_period(period)) == period
    assert previous_period(next_period(period)) == period
    assert previous_period(1) == 365
    assert next_period(365) == 1


def _configured_site(name: str) -> Site:
    root = Path(__file__).resolve().parents[1]
    return next(site for site in load_sites(root / 'config/sites.json') if site.name == name)


@pytest.mark.parametrize(
    ('name', 'source', 'target', 'outside'),
    [
        (
            '呻吟成瘾',
            '绝杀专区 253期:【呻吟成瘾 ☆ 绝杀二肖 ☆ 】\n'
            '253期:〖绝杀二肖〗【鸡羊】开？00中\n'
            '站长宣言: 独立区块到此结束\n253期:〖绝杀二肖〗【牛兔】开00中',
            '【鸡羊】',
            '【牛兔】',
        ),
        (
            '人心如镜',
            '高手帖子 253期: 人心如镜【绝杀二肖】天天有喜\n'
            '人心如镜 发表于 09月10日\n253期：→绝杀二肖←【狗虎】开￥00准\n'
            '七星娱乐\n鸿蒙娱乐城\n澳门永利\n253期：→绝杀二肖←【牛兔】开00准',
            '【狗虎】',
            '【牛兔】',
        ),
        (
            '曹国舅',
            '曹国舅\n『死杀二肖』\n253期死杀二肖 〖鸡-猴 〗 开:0000准\n'
            '最具权 威 港 澳六合彩 58 倍967.cc\n曹国舅\n『平特一肖』\n'
            '253期死杀二肖 〖牛-兔〗 开:0000准',
            '〖鸡-猴 〗',
            '〖牛-兔〗',
        ),
        (
            '科方东文',
            '网红帖 253期: 【绝杀二肖】\n作者:科方东文\n'
            '252期：绝杀2肖 【虎牛】开：牛30错\n253期：绝杀2肖 【鼠猪】开：0000准',
            '【鼠猪】',
            '',
        ),
        (
            '乐不可支',
            '高手料 253期: 乐不可支【绝杀二肖】\n'
            '252期:【绝杀二肖】【虎马】开牛30准\n253期:【绝杀二肖】【牛兔】开0000准\n'
            '站长宣言: 独立区块到此结束\n253期:【绝杀二肖】【鸡羊】开0000准',
            '【牛兔】',
            '【鸡羊】',
        ),
        (
            '梦寐以求第二',
            '253期：【绝杀二肖】〓 更新中奖\n'
            '252期: 《梦寐以求》 👈 绝杀二肖 👈【虎.羊】开:牛30准\n'
            '253期: 《梦寐以求》 👈 绝杀二肖 👈【猴.牛】开:00准',
            '【猴.牛】',
            '',
        ),
    ],
)
def test_production_target_boundaries_are_identity_scoped_and_future_period_safe(
    name, source, target, outside
):
    site = _configured_site(name)
    selection = select_target_block(source, site)
    assert selection is not None
    assert target in selection.text
    if outside:
        assert outside not in selection.text


def test_author_scoped_titles_do_not_treat_historical_rows_as_independent_headers():
    for name, row_text in [
        ('呻吟成瘾', '253期:〖绝杀二肖〗【鸡羊】开？00中'),
        ('人心如镜', '253期：→绝杀二肖←【狗虎】开￥00准'),
    ]:
        site = _configured_site(name)
        assert select_target_block(row_text, site) is None
