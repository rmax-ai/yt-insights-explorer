from __future__ import annotations

import pytest

from yt_insights_web.frontmatter import FrontMatterError, parse_summary, render_markdown


def test_parse_summary_reads_yaml_and_body() -> None:
    parsed = parse_summary(
        """---
type: Digest
title: Example
tags:
  - one
---

## Heading

Body.
"""
    )

    assert parsed.metadata["type"] == "Digest"
    assert parsed.metadata["tags"] == ["one"]
    assert "## Heading" in parsed.markdown


def test_frontmatter_requires_both_delimiters() -> None:
    with pytest.raises(FrontMatterError, match="frontmatter"):
        parse_summary("---\ntype: Digest\n")


def test_markdown_disables_raw_html() -> None:
    rendered = render_markdown("<script>alert('x')</script>\n\n**safe**")

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<strong>safe</strong>" in rendered
