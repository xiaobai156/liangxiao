from __future__ import annotations

import unittest

import pytest

from tests.test_stage4_baseline_parser_contracts import CONTRACT_MODULE


VALIDATION_CONTRACTS = (
    "test_select_record_rejects_same_period_conflict_inside_direction_window",
    "test_select_record_allows_identical_duplicate_inside_direction_window",
    "test_select_record_uses_direction_window_to_separate_period_cycles",
    "test_document_link_with_query_does_not_accept_same_path_different_query",
    "test_document_link_rejects_relative_same_path_on_different_origin",
)


@pytest.mark.parametrize("method_name", VALIDATION_CONTRACTS)
def test_frozen_baseline_validation_contract(method_name: str) -> None:
    case = CONTRACT_MODULE.TwoZodiacSiteScraperTest(method_name)
    result = unittest.TestResult()
    case.run(result)
    if result.failures or result.errors:
        details = "\n".join(message for _case, message in result.failures + result.errors)
        pytest.fail(details)
