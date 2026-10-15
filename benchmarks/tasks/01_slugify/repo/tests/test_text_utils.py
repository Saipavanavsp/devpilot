from text_utils import slugify


def test_simple_sentence():
    assert slugify("Hello World") == "hello-world"
