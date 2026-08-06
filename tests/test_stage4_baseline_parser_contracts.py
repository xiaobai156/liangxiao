from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest

import pytest

from tests import parser_legacy_adapter


BASELINE_TEST = Path(
    r"C:\Users\Administrator\Desktop\每天工具\数据系列\杀两肖-修复版\test_two_zodiac_site_scraper.py"
)


PARSER_CONTRACTS = (
    "test_parse_configured_named_block",
    "test_normalizes_zodiac_separator",
    "test_generic_parser_preserves_same_period_conflict_for_rejection",
    "test_shared_pattern_parser_preserves_same_period_conflict_for_rejection",
    "test_wangzhe_stat_parser_collects_all_repeated_fields_in_one_period_block",
    "test_rejects_non_zodiac_values",
    "test_parse_generic_two_zodiac_records",
    "test_generic_two_zodiac_rejects_plain_pair_without_kill_semantic",
    "test_two_zodiac_rejects_same_zodiac_pair",
    "test_site_scoped_two_zodiac_requires_site_anchor",
    "test_site_scoped_two_zodiac_fails_without_site_anchor",
    "test_site_scoped_two_zodiac_without_stop_does_not_cross_next_column",
    "test_site_scoped_two_zodiac_without_stop_keeps_normal_preamble_and_records",
    "test_parse_current_192_fixed_article_templates",
    "test_parse_liuhebadian_two_zodiac_tail_block",
    "test_parse_wangzhejiudian_two_zodiac_block",
    "test_parse_wangzhejiudian_forbidden_two_zodiac_chart",
    "test_parse_wangzhejiudian_kill_two_zodiac_chart",
    "test_parse_shuqhbq_forbidden_two_zodiac_fields",
    "test_parse_zhuque_forbidden_material_two_zodiac",
    "test_parse_tuku_user_forums_two_zodiac_topic",
    "test_parse_tuku_user_forums_short_kill_topic",
    "test_parse_tuku_user_forums_precise_two_zodiac_topic",
    "test_tuku_user_forums_parsers_use_first_current_snapshot",
    "test_parse_tuku_user_forums_shizhuang_cut_topic",
    "test_parse_wuyou_wulv_topic_records",
    "test_parse_fugui_kede_admin_article",
    "test_parse_songjiu_yingxin_admin_article",
    "test_parse_manager_article_keeps_period_boundary_and_site_anchor",
    "test_manager_article_preserves_same_period_conflict_for_rejection",
    "test_parse_junlin_tianxia_manager_article_uses_dedicated_anchor",
    "test_parse_liuhe_zhongxin_manager_article_rejects_same_period_conflict",
    "test_dedicated_manager_article_parsers_reject_wrong_site_names",
    "test_parse_meifeise_wu_manager_article_uses_plain_name_anchor",
    "test_parse_qinneng_buzhuo_read_page",
    "test_parse_laodazhu_ten_zodiac_complement",
    "test_ten_zodiac_complement_rejects_eleven_items_with_duplicate",
    "test_parse_suiyin_jiliang_ten_zodiac_complement",
    "test_parse_bufeng_zhuoying_topic",
    "test_parse_zhenlong_fankui_topic",
    "test_parse_gaohuo_zhifei_topic_stops_before_next_block",
    "test_topic_parsers_stop_before_different_author_even_when_footer_is_later",
    "test_parse_dedicated_named_blocks_from_new_sites",
    "test_parse_generic_split_line_two_zodiac_records",
    "test_parse_xiangfu_ercheng_tail_rejects_broken_period_cycle",
    "test_parse_xiangfu_ercheng_tail_rejects_duplicate_zodiac",
    "test_parse_xiangfu_ercheng_tail_requires_previous_article_boundary",
    "test_parse_baishou_qijia_tail_rejects_broken_cycle_and_missing_boundary",
    "test_parse_baishou_qijia_tail_rejects_wrong_site_name",
    "test_parse_baishou_qijia_tail_rejects_ascii_brackets",
    "test_parse_yiben_wanli_sisha_bottom_uses_exact_column_and_recent_three",
    "test_parse_yiben_wanli_sisha_bottom_rejects_wrong_column_and_conflict",
    "test_parse_baijie_shujinguang_top_uses_exact_column_and_top_three",
    "test_parse_baijie_shujinguang_top_rejects_missing_boundary_and_conflict",
    "test_parse_baijie_shujinguang_accepts_its_explicit_footer_boundary",
    "test_named_bottom_block_stops_before_next_article",
    "test_named_bottom_without_configured_stop_stops_at_next_plain_article_heading",
    "test_guangxizai_current_domain_anchor_parses_target_period",
    "test_guangdong_current_domain_anchor_parses_target_period",
    "test_guangxizai_rotated_g_domain_anchor_parses_target_period",
    "test_guangxizai_rotated_a_domain_anchor_parses_target_period",
    "test_guangdong_rotated_g_domain_anchor_parses_target_period",
    "test_guangdong_rotated_a_domain_anchor_parses_target_period",
    "test_parent_bound_parsers_do_not_depend_on_publisher_url",
    "test_parent_bound_parsers_reject_heading_record_period_mismatch",
    "test_dengtang_rushi_bottom_parser_uses_exact_block_and_window",
    "test_dengtang_rushi_bottom_parser_rejects_wrong_route_and_period_gap",
    "test_ziranziran_top_parser_uses_named_kill_block_and_dedupes_rendered_copy",
)


def load_baseline_contract_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("frozen_stage4_contracts", BASELINE_TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    old_scraper = sys.modules.get("two_zodiac_site_scraper")
    old_validator = sys.modules.get("validate_failed_sites")
    sys.modules["two_zodiac_site_scraper"] = parser_legacy_adapter
    sys.modules["validate_failed_sites"] = ModuleType("validate_failed_sites")
    try:
        spec.loader.exec_module(module)
    finally:
        if old_scraper is None:
            sys.modules.pop("two_zodiac_site_scraper", None)
        else:
            sys.modules["two_zodiac_site_scraper"] = old_scraper
        if old_validator is None:
            sys.modules.pop("validate_failed_sites", None)
        else:
            sys.modules["validate_failed_sites"] = old_validator
    return module


CONTRACT_MODULE = load_baseline_contract_module()


@pytest.mark.parametrize("method_name", PARSER_CONTRACTS)
def test_frozen_baseline_parser_contract(method_name: str) -> None:
    case = CONTRACT_MODULE.TwoZodiacSiteScraperTest(method_name)
    result = unittest.TestResult()
    case.run(result)
    if result.failures or result.errors:
        details = "\n".join(message for _case, message in result.failures + result.errors)
        pytest.fail(details)
