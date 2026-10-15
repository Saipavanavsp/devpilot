Implement `slugify(text, max_length=None)` in `text_utils.py`.

Rules:
- Convert accented characters to their closest ASCII letters (for example "Café" becomes "cafe") and drop any other non-ASCII characters.
- Lowercase everything.
- Replace every run of characters that are not letters or digits with a single hyphen.
- Remove leading and trailing hyphens.
- If `max_length` is given and the slug is longer, cut it to at most `max_length` characters without leaving a trailing hyphen.

Add tests for the behaviour in `tests/test_text_utils.py`.
