import utils.trade_journal as journal


def test_trade_journal_default_path_is_project_logs(monkeypatch):
    monkeypatch.delenv("TRADE_JOURNAL_FILE", raising=False)
    assert journal._default_path() == journal.PROJECT_ROOT / "logs" / "trade_journal.jsonl"


def test_trade_journal_relative_env_path_resolves_from_project_root(monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_FILE", "logs/custom_journal.jsonl")
    assert journal._default_path() == journal.PROJECT_ROOT / "logs" / "custom_journal.jsonl"
