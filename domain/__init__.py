"""V2 domain contracts."""

from domain.errors import ConfigurationError, ErrorCategory, ScrapeFailure
from domain.models import (
    AdminArticleMatch,
    CacheCoverage,
    DocumentBundle,
    DuplicateFinding,
    HistoryResult,
    PayloadDocument,
    Record,
    Result,
    Site,
)

__all__ = [
    "AdminArticleMatch",
    "CacheCoverage",
    "ConfigurationError",
    "DocumentBundle",
    "DuplicateFinding",
    "ErrorCategory",
    "HistoryResult",
    "PayloadDocument",
    "Record",
    "Result",
    "ScrapeFailure",
    "Site",
]
