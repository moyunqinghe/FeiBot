from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class SkillFile(BaseModel):
    """One file of a skill package. Field names mirror the host's GeneralSkillFile."""

    path: str
    content: str
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class SkillPackage:
    """Normalized skill package resolved from a remote source."""

    files: tuple[SkillFile, ...]
    skill_markdown: str
    metadata: dict[str, object] = field(default_factory=dict)
    name_hint: str | None = None
    slug_hint: str | None = None
    homepage_hint: str | None = None
    source_kind: str = "url"
