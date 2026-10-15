from words import top_words


def test_single_word():
    assert top_words("hello hello", 1) == [("hello", 2)]
