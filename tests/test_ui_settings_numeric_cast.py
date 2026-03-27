from ui.pages.settings import _as_float, _as_int


def test_as_float_handles_int_and_string_values():
    assert _as_float(0, 0.01) == 0.0
    assert _as_float("0.25", 0.01) == 0.25


def test_as_float_falls_back_for_invalid_values():
    assert _as_float("", 0.01) == 0.01
    assert _as_float("abc", 0.01) == 0.01


def test_as_int_handles_int_and_string_values():
    assert _as_int(5, 1) == 5
    assert _as_int("10", 1) == 10


def test_as_int_falls_back_for_invalid_values():
    assert _as_int(None, 7) == 7
    assert _as_int("x", 7) == 7
