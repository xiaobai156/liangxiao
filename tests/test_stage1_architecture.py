from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = (
    "two_zodiac_site_scraper.py",
    "cli.py",
    "domain/__init__.py",
    "domain/models.py",
    "domain/errors.py",
    "domain/identity.py",
    "config/__init__.py",
    "config/loader.py",
    "config/sites.json",
    "fetching/__init__.py",
    "fetching/client.py",
    "fetching/page.py",
    "fetching/dynamic_article.py",
    "fetching/user_forum.py",
    "fetching/browser.py",
    "parsers/__init__.py",
    "parsers/registry.py",
    "parsers/helpers.py",
    "parsers/forum.py",
    "parsers/dynamic.py",
    "parsers/chart.py",
    "parsers/special.py",
    "validation/__init__.py",
    "validation/direction.py",
    "validation/boundaries.py",
    "validation/records.py",
    "validation/conflicts.py",
    "cache/__init__.py",
    "cache/contracts.py",
    "cache/repository.py",
    "cache/guard.py",
    "cache/duplicates.py",
    "services/__init__.py",
    "services/single_period.py",
    "services/multi_period.py",
    "services/recent_history.py",
    "services/failed_site_validation.py",
    "output/__init__.py",
    "output/formatter.py",
    "output/transaction.py",
    "diagnostics/__init__.py",
    "diagnostics/run_report.py",
    "tests/test_stage8_full_configuration_audit.py",
)


def test_final_v2_skeleton_exists() -> None:
    missing = [relative for relative in REQUIRED_FILES if not (ROOT / relative).is_file()]
    assert missing == []


def test_project_runtime_is_pinned_to_python_310() -> None:
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.10"


def test_compatibility_entrypoint_is_thin() -> None:
    source = (ROOT / "two_zodiac_site_scraper.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    assert len(lines) <= 50
    assert "from cli import main" in source
    assert "raise SystemExit(main())" in source
    for forbidden in ("requests", "urllib3", "recent_10_cache", "sites.json", "def parse_"):
        assert forbidden not in source


def test_golden_behavior_contract_is_complete() -> None:
    contract = json.loads((ROOT / "tests/golden/behavior_contract.json").read_text(encoding="utf-8"))
    assert contract["site_count"] == 215
    assert contract["defaults"]["workers"] == 10
    assert contract["period_rules"] == {"digits": 3, "minimum": 1, "maximum": 365}
    assert contract["direction_window"] == 3
    assert contract["paths"]["success_name"] == "{period}期-二肖.txt"
    assert contract["paths"]["failure_name"] == "{period}期-二肖-失败.txt"
    assert len(contract["single_period_fixed_tail"]) == 5
    assert "数据存在冲突" in contract["error_categories"]


def test_v2_entrypoint_exposes_cli_without_running_formal_scrape() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "two_zodiac_site_scraper.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0
    assert "统一抓取杀两肖站点数据" in completed.stdout
