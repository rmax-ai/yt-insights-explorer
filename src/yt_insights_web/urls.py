"""Relative URL helpers for pages that can be opened from file://."""

from __future__ import annotations

import posixpath


def normalize_base_path(base_path: str) -> str:
    value = (base_path or "./").strip()
    if value in {".", "./", ""}:
        return "./"
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") + "/"


def relative_url(current_page: str, target_page: str) -> str:
    current_dir = posixpath.dirname(current_page) or "."
    value = posixpath.relpath(target_page, current_dir)
    return "index.html" if value == "." else value


def page_url(target_page: str, base_path: str = "./") -> str:
    base = normalize_base_path(base_path)
    if base == "./":
        return target_page
    return f"{base}{target_page.lstrip('/')}"


def asset_url(current_page: str, asset_path: str, base_path: str = "./") -> str:
    base = normalize_base_path(base_path)
    if base == "./":
        return relative_url(current_page, asset_path)
    return page_url(asset_path, base)
