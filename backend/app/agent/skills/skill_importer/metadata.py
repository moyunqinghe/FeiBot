from __future__ import annotations

import re
from urllib.parse import urlparse


def parse_skill_metadata(markdown: str) -> dict[str, object]:
    """Parse single-line YAML-like frontmatter (``---`` block).

    Only supports one ``key: value`` per line, quoted/scalar string values and
    ``[a, b]`` comma-separated lists. Multi-line values and comma-in-quotes
    are not supported.
    """
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, object] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = _parse_metadata_value(value.strip())
    return metadata


def _parse_metadata_value(value: str) -> object:
    cleaned = value.strip().strip("'\"")
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return [item.strip().strip("'\"") for item in cleaned[1:-1].split(",") if item.strip()]
    return cleaned


def metadata_text(metadata: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return slug or "general-skill"


def source_name(source: str) -> str:
    """Derive a display-name hint from a source string (last path segment)."""
    parsed = urlparse(source)
    path = parsed.path if parsed.scheme else source
    cleaned = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".zip").removesuffix(".md")
    return cleaned or "open-platform-skill"
