from text_utils import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_accents_are_transliterated():
    assert slugify("Café Déjà Vu") == "cafe-deja-vu"


def test_punctuation_runs_collapse():
    assert slugify("  Rock & Roll -- 2024!!  ") == "rock-roll-2024"


def test_non_latin_characters_are_dropped():
    assert slugify("hello 世界 world") == "hello-world"


def test_empty_and_symbol_only():
    assert slugify("") == ""
    assert slugify("!!!") == ""


def test_max_length_has_no_trailing_hyphen():
    result = slugify("The quick brown fox", max_length=10)
    assert len(result) <= 10
    assert not result.endswith("-")
    assert "the-quick-brown-fox".startswith(result)


def test_max_length_longer_than_slug():
    assert slugify("short", max_length=50) == "short"
