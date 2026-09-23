"""Normalization offset mapping tests."""
from app.core.normalization import normalize


class TestNormalization:
    def test_identity(self):
        n = normalize("hello world")
        assert n.normalized == "hello world"
        assert n.orig_interval_for(0, 5) == (0, 5)

    def test_nbsp_to_space(self):
        n = normalize("a\u00a0b")
        assert n.normalized == "a b"
        # NBSP is a single original char at index 1.
        assert n.orig_interval_for(1, 2) == (1, 2)

    def test_dash_variants(self):
        n = normalize("a\u2014b")
        assert n.normalized == "a-b"
        assert n.orig_interval_for(1, 2) == (1, 2)

    def test_reverse_mapping_preserves_original(self):
        text = "г. Москва, ул. Тверская"
        n = normalize(text)
        # Find "Москва" case-insensitively.
        idx = n.find("Москва")
        assert idx != -1
        start, end = n.orig_interval_for(idx, idx + len("Москва"))
        assert text[start:end] == "Москва"

    def test_emoji_before_span(self):
        # Emoji is a single code point in Python (BMP surrogate pair in UTF-16).
        text = "😀 Иван Петров"
        n = normalize(text)
        idx = n.find("Иван")
        start, end = n.orig_interval_for(idx, idx + len("Иван"))
        assert text[start:end] == "Иван"
        # Emoji occupies original index 0.
        assert start == 2

    def test_out_of_range_raises(self):
        n = normalize("abc")
        try:
            n.orig_interval_for(0, 10)
            assert False, "should raise"
        except ValueError:
            pass
