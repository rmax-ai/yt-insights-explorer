"""Source-version adapters for the corpus compiler."""

from .v1 import adapt_source, adapt_v1, adapt_v1_record
from .v2 import (
    V2SourceRecord,
    V2ValidationError,
    adapt_v2,
    validate_v2_insights,
    validate_v2_summary,
)

__all__ = [
    "V2SourceRecord",
    "V2ValidationError",
    "adapt_source",
    "adapt_v1",
    "adapt_v1_record",
    "adapt_v2",
    "validate_v2_insights",
    "validate_v2_summary",
]
