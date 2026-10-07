from torqrun_agent.redact import MASK, Redactor


def test_masks_every_occurrence_longest_first() -> None:
    r = Redactor(["hunter2-secret", "hunter2"])
    assert r("pw=hunter2-secret and hunter2") == f"pw={MASK} and {MASK}"


def test_multiline_values_are_masked_per_line() -> None:
    r = Redactor(["line-one\nline-two"])
    assert r("x line-two y\n") == f"x {MASK} y\n"


def test_tiny_values_are_ignored() -> None:
    r = Redactor(["ab", ""])
    assert not r
    assert r("abc") == "abc"


def test_base64_form_is_masked() -> None:
    import base64

    encoded = base64.b64encode(b"hunter2-secret").decode()
    assert Redactor(["hunter2-secret"])(f"token {encoded}") == f"token {MASK}"
