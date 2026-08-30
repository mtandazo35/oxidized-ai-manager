from app.scheduler import is_due


def test_is_due_respects_interval() -> None:
    assert is_due(60 * 60, 60) is True
    assert is_due(59 * 60, 60) is False


def test_is_due_enforces_minimum_of_five_minutes() -> None:
    assert is_due(4 * 60, 1) is False
    assert is_due(5 * 60, 0) is True
