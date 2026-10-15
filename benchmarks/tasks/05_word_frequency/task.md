Implement `top_words(text, k)` in `words.py`.

It returns the `k` most frequent words as a list of `(word, count)` tuples.
- Words are maximal runs of ASCII letters and apostrophes, compared case-insensitively and returned in lowercase. Apostrophes at the start or end of a word are removed ("'tis" becomes "tis", "dogs'" becomes "dogs"); apostrophes inside a word are kept ("don't").
- Order by count descending, then alphabetically for ties.
- If there are fewer than `k` distinct words, return all of them. `k <= 0` returns an empty list.

Add tests in `tests/test_words.py`.
