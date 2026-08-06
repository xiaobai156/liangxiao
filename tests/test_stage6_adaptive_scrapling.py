import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import requests

import adaptive_scrapling as adaptive
from domain.models import POSITION_KIND_VISIBLE_TEXT, DocumentBundle, PayloadDocument


def hold_profile_process_lock(path, ready, release):
    store = adaptive.StructureProfileStore(Path(path))
    with store._process_lock():
        ready.set()
        release.wait(5)


@dataclass(frozen=True)
class FakeRecord:
    period: int
    zodiac: str
    position: int
    position_kind: str = POSITION_KIND_VISIBLE_TEXT
    source_positions: tuple[int, ...] = ()


ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"


def parse_records(source, site):
    import re

    if isinstance(source, PayloadDocument):
        source = source.source
    if site.name not in source or "测试栏目" not in source:
        return []
    pattern = re.compile(rf"(?P<period>\d{{3}})期[^【]*【(?P<zodiac>[{ZODIACS}]{{2}})】")
    return [
        FakeRecord(int(match.group("period")), match.group("zodiac"), position)
        for position, match in enumerate(pattern.finditer(source))
        if match.group("zodiac")[0] != match.group("zodiac")[1]
    ]


def select_record(records, period, site):
    if not records:
        raise ValueError("未抓到有效候选")
    candidates = records[:3] if site.pick == "top" else records[-3:]
    for record in candidates:
        if record.period == period:
            return record
    raise ValueError(f"{site.pick} 候选内未找到 {period} 期")


def validate_record(record):
    return (
        len(record.zodiac) == 2
        and len(set(record.zodiac)) == 2
        and all(value in ZODIACS for value in record.zodiac)
    )


def make_site(**overrides):
    values = {
        "name": "测试站",
        "pick": "bottom",
        "url": "https://example.test/topic/1",
        "parser": "dedicated_parser",
        "payload": "page",
        "title": "测试站测试栏目",
        "keywords": ("测试站", "测试栏目"),
        "profile_id": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def as_bundle(*items):
    return DocumentBundle(
        tuple(
            PayloadDocument(label, url, source, record_id="topic:1")
            for label, url, source in items
        )
    )


OLD_HTML = """
<html><body><main>
  <section class="old-material">
    <h2>测试站 测试栏目</h2>
    <p>197期【猴狗】</p><p>198期【牛马】</p><p>199期【鸡牛】</p>
  </section>
</main></body></html>
"""


MOVED_HTML = """
<html><body><div class="layout"><aside>广告</aside><main>
  <section class="new-material">
    <header><h2>测试站 测试栏目</h2></header>
    <p>197期【猴狗】</p><p>198期【牛马】</p><p>199期【鸡牛】</p>
  </section>
</main></div></body></html>
"""


class FailureClassificationTest(unittest.TestCase):
    def test_classifies_network_and_content_failures_in_chinese(self):
        self.assertEqual(adaptive.classify_failure(requests.Timeout("late")), "网络请求超时")
        self.assertEqual(adaptive.classify_failure(requests.exceptions.SSLError("tls")), "SSL连接失败")
        self.assertEqual(
            adaptive.classify_failure(requests.RequestException("curl TLS1.0 SSL兼容抓取失败")),
            "SSL连接失败",
        )
        self.assertEqual(
            adaptive.classify_failure(
                requests.RequestException("连接失败：https://ssl-data.example/path")
            ),
            "HTTP请求失败",
        )
        self.assertEqual(
            adaptive.classify_failure(ValueError("bottom 候选内未找到 199 期")),
            "内容尚未发布",
        )
        self.assertEqual(
            adaptive.classify_failure(ValueError("top 列表候选内未找到 199 期")),
            "内容尚未发布",
        )
        self.assertEqual(adaptive.classify_failure(ValueError("未找到测试站目标脚本")), "页面结构已变化")
        self.assertEqual(adaptive.classify_failure(ValueError("未抓到有效候选")), "未找到指定目标")

    def test_only_structural_failures_enter_adaptive_recovery(self):
        site = make_site()
        self.assertTrue(adaptive.should_attempt_recovery(site, "页面结构已变化", "目标脚本已变化"))
        self.assertFalse(adaptive.should_attempt_recovery(site, "未找到指定目标", "未抓到有效候选"))
        self.assertFalse(adaptive.should_attempt_recovery(site, "内容尚未发布", "候选内没有当期"))
        self.assertFalse(adaptive.should_attempt_recovery(site, "SSL连接失败", "tls"))

    def test_admin_api_non_404_empty_payload_does_not_bypass_existing_rule(self):
        site = make_site(payload="admin_article_api", url="https://example.test/article/admin/abc")
        self.assertFalse(
            adaptive.should_attempt_recovery(
                site,
                "未找到指定目标",
                "接口返回内容内未找到对应后台文章",
            )
        )


class StructureProfileStoreTest(unittest.TestCase):
    def test_registers_independent_structure_profile_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site_structure_profiles.json"
            store = adaptive.StructureProfileStore(path)
            site = make_site()

            store.register_sites([site])
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["sources"]), 1)
        entry = next(iter(payload["sources"].values()))
        self.assertEqual(entry["name"], "测试站")
        self.assertIn("anchors", entry)
        self.assertNotIn("values", entry)
        self.assertNotIn("records", entry)

    def test_primary_success_creates_trusted_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site_structure_profiles.json"
            manager = adaptive.AdaptiveManager(path, mode="fallback", similarity=70)
            site = make_site()
            records = parse_records(OLD_HTML, site)
            selected = select_record(records, 199, site)

            manager.observe_primary_success(site, OLD_HTML, records, selected, parse_records, select_record)
            entry = manager.store.get_site(site)

        self.assertEqual(entry["status"], "trusted")
        self.assertEqual(entry["success_count"], 1)
        self.assertTrue(entry["structure_fingerprint"])
        self.assertIn(adaptive.TARGET_IDENTIFIER, entry["trusted_elements"])

    def test_profile_write_refuses_to_overwrite_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site_structure_profiles.json"
            path.write_text("{broken", encoding="utf-8")
            store = adaptive.StructureProfileStore(path)

            with self.assertRaises(adaptive.ProfileStoreError):
                store.register_sites([make_site()])

            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_primary_success_without_verified_element_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site_structure_profiles.json"
            store = adaptive.StructureProfileStore(path)
            site = make_site()

            store.mark_primary_success(
                site,
                adaptive.structural_fingerprint("plain text without an element"),
                None,
                adaptive.TARGET_IDENTIFIER,
                (site.url,),
            )
            entry = store.get_site(site)

        self.assertEqual(entry["status"], "observed")
        self.assertNotIn(adaptive.TARGET_IDENTIFIER, entry["trusted_elements"])
        self.assertFalse(entry["structure_fingerprint"])

    def test_structure_profile_redacts_period_zodiac_and_dynamic_attributes(self):
        selector = adaptive.Selector(
            """
            <div data-period="199">
              <p class="record-199" title="199期鸡牛">测试站 199期【鸡牛】</p>
            </div>
            """
        )
        profile = adaptive.element_to_profile(selector.css("p")[0])
        serialized = json.dumps(profile, ensure_ascii=False)

        self.assertIn("测试站", serialized)
        self.assertNotIn("199", serialized)
        self.assertNotIn("鸡牛", serialized)

    def test_profile_redacts_single_and_separated_zodiac_mentions(self):
        cases = (
            "当期生肖：牛",
            "当期不要牛和马",
            "杀肖（牛）另一个马",
            "199期生肖：馬雞、豬龍",
        )
        zodiac_variants = "牛馬马羊雞鸡狗豬猪鼠虎兔龍龙蛇猴"
        for text in cases:
            with self.subTest(text=text):
                selector = adaptive.Selector(f'<p title="{text}">{text}</p>')
                serialized = json.dumps(adaptive.element_to_profile(selector.css("p")[0]), ensure_ascii=False)
                for zodiac in zodiac_variants:
                    self.assertNotIn(zodiac, serialized)

    def test_failure_profile_redacts_business_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = adaptive.StructureProfileStore(Path(tmp) / "site_structure_profiles.json")
            site = make_site()
            store.mark_failure(site, adaptive.FAILURE_DATA_CONFLICT, "199期鸡牛@位置2")
            entry = store.get_site(site)

        detail = entry["last_failure"]["detail"]
        self.assertNotIn("199", detail)
        self.assertNotIn("鸡牛", detail)
        self.assertEqual(entry["last_failure"]["category"], adaptive.FAILURE_DATA_CONFLICT)

    def test_profile_updates_wait_for_another_process_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site_structure_profiles.json"
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=hold_profile_process_lock,
                args=(str(path), ready, release),
            )
            process.start()
            self.assertTrue(ready.wait(5), "子进程未取得结构档案锁")
            timer = threading.Timer(0.3, release.set)
            timer.start()
            started = time.monotonic()
            try:
                adaptive.StructureProfileStore(path).register_sites([make_site()])
            finally:
                release.set()
                timer.cancel()
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)

        self.assertEqual(process.exitcode, 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)


class AdaptiveManagerTest(unittest.TestCase):
    def test_document_discovery_rejects_non_http_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json")
            site = make_site()
            fetched = []

            def fetch(url, timeout):
                fetched.append(url)
                return "payload"

            source = """
            <script src="javascript:alert(1)"></script>
            <script src="data:text/javascript,alert(2)"></script>
            <iframe src="mailto:test@example.com"></iframe>
            <script src="/valid.js"></script>
            """
            manager.collect_documents(site, 1, source, fetch)

        self.assertEqual(fetched, ["https://example.test/valid.js"])

    def test_document_collection_respects_strict_maximum_including_decoded_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(
                Path(tmp) / "site_structure_profiles.json",
                max_documents=2,
            )
            site = make_site()
            source = '<script src="/one.js"></script>'

            documents = manager.collect_documents(
                site,
                1,
                source,
                lambda url, timeout: "encoded script",
                lambda value: "decoded script",
            )

        self.assertEqual(len(documents.documents), 2)
        self.assertEqual([item.label for item in documents.documents], ["原始页面", "脚本"])

    def test_document_collection_keeps_parent_link_and_record_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json")
            site = make_site()
            source = '<script src="/material.js"></script>'

            documents = manager.collect_documents(
                site,
                1,
                source,
                lambda url, timeout: MOVED_HTML,
            )

        self.assertIsInstance(documents, DocumentBundle)
        root, script = documents.documents
        self.assertEqual(root.record_id, "topic:1")
        self.assertEqual(script.record_id, "topic:1")
        self.assertEqual(script.parent_url, site.url)
        self.assertEqual(script.link_reference, "/material.js")

    def test_recovery_rejects_child_document_without_parent_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json")
            site = make_site()
            bundle = DocumentBundle(
                (
                    PayloadDocument(
                        "孤立脚本",
                        "https://example.test/material.js",
                        MOVED_HTML,
                        record_id="topic:1",
                        parent_url=site.url,
                        link_reference="/material.js",
                    ),
                )
            )

            with self.assertRaisesRegex(ValueError, "父文档.*不在DocumentBundle"):
                manager.recover_from_documents(
                    site,
                    199,
                    bundle,
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_browser_render_failure_is_retained_when_no_document_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json")
            site = make_site(url="https://example.test/article/manager/abc")

            def render(url, timeout):
                raise requests.RequestException("浏览器进程启动失败")

            documents = manager.collect_documents(
                site,
                1,
                "<html><body>空壳</body></html>",
                lambda url, timeout: "",
                render_text=render,
            )
            with self.assertRaises(adaptive.AdaptiveRecoveryError) as raised:
                manager.recover_from_documents(
                    site,
                    199,
                    documents,
                    parse_records,
                    select_record,
                    validate_record,
                )

        self.assertEqual(raised.exception.category, adaptive.FAILURE_BROWSER)
        self.assertIn("浏览器进程启动失败", raised.exception.detail)

    def test_scrapling_relocates_saved_section_after_dom_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()
            records = parse_records(OLD_HTML, site)
            manager.observe_primary_success(
                site,
                OLD_HTML,
                records,
                select_record(records, 199, site),
                parse_records,
                select_record,
            )

            relocated = manager.adaptive_candidate_sources(site, MOVED_HTML)

        self.assertTrue(relocated)
        self.assertTrue(any("199期" in source and "测试栏目" in source for source in relocated))

    def test_recovery_accepts_one_exact_valid_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            recovery = manager.recover_from_documents(
                site,
                199,
                as_bundle(("页面", site.url, MOVED_HTML)),
                parse_records,
                select_record,
                validate_record,
            )

        self.assertEqual(recovery.record.zodiac, "鸡牛")
        self.assertEqual(recovery.record.position, 2)
        self.assertEqual(len(recovery.records), 3)

    def test_elementless_adaptive_candidate_never_becomes_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "site_structure_profiles.json"
            site = make_site()

            adaptive.AdaptiveManager(profile).recover_from_documents(
                site, 199, as_bundle(("页面", site.url, MOVED_HTML)),
                parse_records, select_record, validate_record,
            )
            second = adaptive.AdaptiveManager(profile)
            second.recover_from_documents(
                site, 199, as_bundle(("页面", site.url, MOVED_HTML)),
                parse_records, select_record, validate_record,
            )
            entry = second.store.get_site(site)

        self.assertEqual(entry["status"], "candidate")
        self.assertFalse(entry["structure_fingerprint"])
        self.assertNotIn(adaptive.TARGET_IDENTIFIER, entry["trusted_elements"])

    def test_recovery_rejects_historical_conflict_across_successful_documents(self):
        conflicting_history = MOVED_HTML.replace("198期【牛马】", "198期【蛇马】")
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "数据存在冲突"):
                manager.recover_from_documents(
                    site,
                    199,
                    as_bundle(
                        ("文档一", site.url, MOVED_HTML),
                        ("文档二", site.url + "/copy", conflicting_history),
                    ),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_includes_non_target_document_in_history_conflict_check(self):
        without_target = MOVED_HTML.replace(
            '<p>197期【猴狗】</p><p>198期【牛马】</p><p>199期【鸡牛】</p>',
            '<p>196期【虎兔】</p><p>197期【猴狗】</p><p>198期【蛇马】</p>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "数据存在冲突.*198期"):
                manager.recover_from_documents(
                    site,
                    199,
                    as_bundle(("目标文档", site.url, MOVED_HTML), ("旁证文档", site.url + "/copy", without_target)),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_ignores_previous_cycle_conflict_outside_each_document_window(self):
        current = MOVED_HTML.replace(
            '<p>197期【猴狗】</p>',
            '<p>198期【蛇马】</p><p>197期【猴狗】</p>',
        )
        old_copy = current.replace('198期【蛇马】', '198期【兔龙】', 1)
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            recovery = manager.recover_from_documents(
                site,
                199,
                as_bundle(("当前文档", site.url, current), ("旧周期副本", site.url + "/old", old_copy)),
                parse_records,
                select_record,
                validate_record,
            )

        self.assertEqual((recovery.record.period, recovery.record.zodiac, recovery.record.position), (199, "鸡牛", 3))

    def test_recovery_does_not_ignore_a_document_with_target_window_conflict(self):
        conflicting = MOVED_HTML.replace(
            '</section>',
            '<p>199期【蛇马】</p></section>',
        )

        def strict_select(records, period, site):
            candidates = records[:3] if site.pick == "top" else records[-3:]
            matches = [record for record in candidates if record.period == period]
            signatures = {(record.zodiac, record.position) for record in matches}
            if len(signatures) > 1:
                raise ValueError("数据存在冲突：同期不同结果")
            if not matches:
                raise ValueError(f"{site.pick} 候选内未找到 {period} 期")
            return matches[0]

        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "数据存在冲突"):
                manager.recover_from_documents(
                    site,
                    199,
                    as_bundle(("冲突文档", site.url, conflicting), ("干净文档", site.url + "/clean", MOVED_HTML)),
                    parse_records,
                    strict_select,
                    validate_record,
                )

    def test_recovery_rejects_same_period_value_conflict(self):
        conflict = MOVED_HTML.replace("199期【鸡牛】", "199期【蛇马】")
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "数据存在冲突"):
                manager.recover_from_documents(
                    site,
                    199,
                    as_bundle(("文档一", site.url, MOVED_HTML), ("文档二", site.url + "/copy", conflict)),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_allows_same_value_at_different_document_positions(self):
        shifted = MOVED_HTML.replace(
            '<p>197期【猴狗】</p>',
            '<p>196期【虎兔】</p><p>197期【猴狗】</p>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()

            recovery = manager.recover_from_documents(
                site,
                199,
                DocumentBundle(
                    (
                        PayloadDocument("文档一", site.url, MOVED_HTML, record_id="topic:1"),
                        PayloadDocument("文档二", site.url + "/copy", shifted, record_id="topic:1"),
                    )
                ),
                parse_records,
                select_record,
                validate_record,
            )

        self.assertEqual(recovery.record.zodiac, "鸡牛")

    def test_first_adaptive_structure_is_candidate_not_trusted_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = adaptive.AdaptiveManager(Path(tmp) / "site_structure_profiles.json", mode="fallback")
            site = make_site()
            old_records = parse_records(OLD_HTML, site)
            manager.observe_primary_success(
                site,
                OLD_HTML,
                old_records,
                select_record(old_records, 199, site),
                parse_records,
                select_record,
            )
            old_fingerprint = manager.store.get_site(site)["structure_fingerprint"]

            manager.recover_from_documents(
                site,
                199,
                as_bundle(("新结构", site.url, MOVED_HTML)),
                parse_records,
                select_record,
                validate_record,
            )
            entry = manager.store.get_site(site)

        self.assertEqual(entry["structure_fingerprint"], old_fingerprint)
        self.assertEqual(entry["candidate_profiles"][0]["success_count"], 1)

    def test_adaptive_candidate_needs_two_independent_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "site_structure_profiles.json"
            site = make_site()
            first = adaptive.AdaptiveManager(profile, mode="fallback")
            old_records = parse_records(OLD_HTML, site)
            first.observe_primary_success(
                site,
                OLD_HTML,
                old_records,
                select_record(old_records, 199, site),
                parse_records,
                select_record,
            )
            first.recover_from_documents(
                site, 199, as_bundle(("新结构", site.url, MOVED_HTML)),
                parse_records, select_record, validate_record,
            )
            first.recover_from_documents(
                site, 199, as_bundle(("新结构", site.url, MOVED_HTML)),
                parse_records, select_record, validate_record,
            )
            entry_after_same_run = first.store.get_site(site)
            self.assertEqual(entry_after_same_run["candidate_profiles"][0]["success_count"], 1)
            candidate_fingerprint = entry_after_same_run["candidate_profiles"][0]["structure_fingerprint"]

            second = adaptive.AdaptiveManager(profile, mode="fallback")
            second.recover_from_documents(
                site, 199, as_bundle(("新结构", site.url, MOVED_HTML)),
                parse_records, select_record, validate_record,
            )
            entry_after_new_run = second.store.get_site(site)

        self.assertEqual(entry_after_new_run["structure_fingerprint"], candidate_fingerprint)
        self.assertIn(adaptive.TARGET_IDENTIFIER, entry_after_new_run["trusted_elements"])
        self.assertEqual(entry_after_new_run["candidate_profiles"], [])

    def test_history_candidate_conflict_is_rejected_even_when_one_record_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            TrustedHistoryCrossCheckTest.write_cache(
                cache, site, {"199": "鸡牛", "198": "牛马"}, {"199": 2, "198": 1}
            )
            conflicting = MOVED_HTML.replace(
                "</section>",
                "<p>198期【蛇马】</p></section>",
            )
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "数据存在冲突"):
                manager.recover_from_documents(
                    site, 199, as_bundle(("页面", site.url, conflicting)),
                    parse_records, select_record, validate_record,
                )

    def test_duplicate_history_site_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            payload = {
                "schema": 2,
                "position_kind": POSITION_KIND_VISIBLE_TEXT,
                "issues": [199, 198],
                "sites": [
                    {
                        "name": site.name,
                        "url": site.url,
                        "pick": site.pick,
                        "position_kind": POSITION_KIND_VISIBLE_TEXT,
                        "values": {"199": "鸡牛", "198": "牛马"},
                        "positions": {"199": 2, "198": 1},
                    },
                    {
                        "name": site.name,
                        "url": site.url,
                        "pick": site.pick,
                        "position_kind": POSITION_KIND_VISIBLE_TEXT,
                        "values": {"199": "鸡牛", "198": "牛马"},
                        "positions": {"199": 2, "198": 1},
                    },
                ],
            }
            cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "历史可信数据校验未通过"):
                manager.recover_from_documents(
                    site, 199, as_bundle(("页面", site.url, MOVED_HTML)),
                    parse_records, select_record, validate_record,
                )


class TrustedHistoryCrossCheckTest(unittest.TestCase):
    @staticmethod
    def write_cache(path, site, values, positions):
        path.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "position_kind": POSITION_KIND_VISIBLE_TEXT,
                    "issues": [int(period) for period in list(values)[:2]],
                    "sites": [
                        {
                            "name": site.name,
                            "url": site.url,
                            "pick": site.pick,
                            "position_kind": POSITION_KIND_VISIBLE_TEXT,
                            "values": values,
                            "positions": positions,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_recovery_accepts_when_latest_two_history_has_exact_value_and_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            self.write_cache(cache, site, {"199": "鸡牛", "198": "牛马"}, {"199": 2, "198": 1})
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            recovery = manager.recover_from_documents(
                site,
                199,
                as_bundle(("页面", site.url, MOVED_HTML)),
                parse_records,
                select_record,
                validate_record,
            )

        self.assertEqual(recovery.record.zodiac, "鸡牛")

    def test_recovery_rejects_legacy_cache_position_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            cache.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "issues": [199, 198],
                        "sites": [
                            {
                                "name": site.name,
                                "url": site.url,
                                "pick": site.pick,
                                "values": {"199": "鸡牛", "198": "牛马"},
                                "positions": {"199": 2, "198": 1},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "位置语义不兼容"):
                manager.recover_from_documents(
                    site,
                    199,
                    DocumentBundle((PayloadDocument("页面", site.url, MOVED_HTML, record_id="topic:1"),)),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_rejects_when_history_value_or_position_disagrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            self.write_cache(cache, site, {"199": "鸡牛", "198": "牛马"}, {"199": 7, "198": 8})
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "历史可信数据校验未通过"):
                manager.recover_from_documents(
                    site,
                    199,
                    as_bundle(("页面", site.url, MOVED_HTML)),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_rejects_one_latest_period_mismatch_even_if_other_period_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            site = make_site()
            self.write_cache(
                cache,
                site,
                {"200": "牛马", "199": "狗猪"},
                {"200": 2, "199": 1},
            )
            source = """
            <section><h2>测试站 测试栏目</h2>
            <p>198期【猴狗】</p><p>199期【狗猪】</p><p>200期【鼠兔】</p>
            </section>
            """
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=cache,
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "历史可信数据校验未通过"):
                manager.recover_from_documents(
                    site,
                    200,
                    as_bundle(("页面", site.url, source)),
                    parse_records,
                    select_record,
                    validate_record,
                )

    def test_recovery_rejects_when_required_history_cache_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = adaptive.AdaptiveManager(
                root / "site_structure_profiles.json",
                mode="fallback",
                history_cache_path=root / "missing.json",
                require_history=True,
            )

            with self.assertRaisesRegex(adaptive.AdaptiveRecoveryError, "历史可信数据校验未通过"):
                manager.recover_from_documents(
                    make_site(),
                    199,
                    as_bundle(("页面", "https://example.test", MOVED_HTML)),
                    parse_records,
                    select_record,
                    validate_record,
                )

if __name__ == "__main__":
    unittest.main()






