from words import top_words


def test_counts_case_insensitively():
    assert top_words("The cat and the hat. THE end", 1) == [("the", 3)]


def test_ties_are_alphabetical():
    assert top_words("b a c b a c d", 3) == [("a", 2), ("b", 2), ("c", 2)]


def test_apostrophes():
    text = "Don't stop. 'Tis the dogs' bone, don't"
    assert top_words(text, 2) == [("don't", 2), ("bone", 1)]
    assert ("tis", 1) in top_words(text, 10)
    assert ("dogs", 1) in top_words(text, 10)


def test_digits_and_punctuation_split_words():
    assert top_words("abc123def abc--def", 2) == [("abc", 2), ("def", 2)]


def test_fewer_words_than_k():
    assert top_words("one two", 5) == [("one", 1), ("two", 1)]


def test_non_positive_k_and_empty_text():
    assert top_words("a a a", 0) == []
    assert top_words("a a a", -1) == []
    assert top_words("", 3) == []
    assert top_words("''' !!!", 3) == []
