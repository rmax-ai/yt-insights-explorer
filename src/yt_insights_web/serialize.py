"""Canonical UTF-8 serialization for generated JSON."""

from __future__ import annotations

import json
from typing import Any


def json_text(value: Any) -> str:
    """Serialize JSON with the repository determinism contract."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def embedded_json_text(value: Any) -> str:
    """Serialize JSON for an HTML script block without closing markup."""

    return json_text(value).rstrip("\n").replace("<", "\\u003c")
