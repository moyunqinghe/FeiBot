from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict


class SkillFile(BaseModel):
    """One file of a skill package. Field names mirror the host's GeneralSkillFile."""

    model_config = ConfigDict(frozen=True)

    path: str
    content: str
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class SkillPackage:
    """Normalized skill package resolved from a remote source.

    source_kind: "platform" | "github" | "url"（github 含 owner/repo 简写来源），
    默认 "url"。
    """

    files: tuple[SkillFile, ...]
    skill_markdown: str
    metadata: dict[str, object] = field(default_factory=dict)
    name_hint: str | None = None
    slug_hint: str | None = None
    homepage_hint: str | None = None
    source_kind: str = "url"
