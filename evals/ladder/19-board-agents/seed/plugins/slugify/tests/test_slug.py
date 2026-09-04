from slugify.slugify import slug


def test_words_are_joined_by_hyphens():
    assert slug("Hello World") == "hello-world"


def test_punctuation_becomes_a_single_hyphen():
    assert slug("Rock & Roll!") == "rock-roll"


def test_runs_of_separators_collapse():
    assert slug("a   b -- c") == "a-b-c"


def test_accents_are_folded():
    assert slug("Café déjà vu") == "cafe-deja-vu"


def test_nothing_hangs_off_the_ends():
    assert slug("  --tidy--  ") == "tidy"
