from skill_importer.metadata import (
    metadata_text,
    parse_skill_metadata,
    slugify,
    source_name,
)


def test_parse_full_frontmatter() -> None:
    md = "---\nname: 天气包\nslug: weather-pack\ntags: [a, b]\n---\n\n# body\n"
    meta = parse_skill_metadata(md)
    assert meta["name"] == "天气包"
    assert meta["slug"] == "weather-pack"
    assert meta["tags"] == ["a", "b"]


def test_parse_no_frontmatter_returns_empty() -> None:
    assert parse_skill_metadata("# no frontmatter\n") == {}


def test_parse_ignores_malformed_lines() -> None:
    md = "---\n# comment\nbroken line\nkey: value\n---\n"
    assert parse_skill_metadata(md) == {"key": "value"}


def test_metadata_text_skips_non_string_and_empty() -> None:
    meta = {"name": "", "title": "Hello", "count": 3}
    assert metadata_text(meta, "name", "title") == "Hello"
    assert metadata_text(meta, "count") is None


def test_slugify() -> None:
    assert slugify("Hello World! 你好") == "hello-world"
    assert slugify("   ") == "general-skill"


def test_source_name() -> None:
    assert source_name("https://example.com/weather.zip") == "weather"
    assert source_name("weather-pack") == "weather-pack"
