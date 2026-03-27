import pytest

from main import TradingBot


@pytest.fixture
def base_config():
    return {
        "mt5": {"timeout": 60000},
        "risk": {"max_daily_loss": 5.0, "capital_base": 0.0},
        "trading": {"check_interval": 1},
        "logging": {"level": "INFO"},
        "ui": {"theme": "light"},
        "alerts": {"telegram": {"enabled": False}, "discord": {"enabled": False}},
    }


def test_yaml_risk_config_applies_when_env_not_set(monkeypatch, base_config):
    monkeypatch.delenv("MAX_DAILY_LOSS", raising=False)
    bot = TradingBot()
    bot.config = base_config

    bot._merge_config({"risk": {"max_daily_loss": 2.5}})

    assert bot.config["risk"]["max_daily_loss"] == 2.5


def test_env_keeps_precedence_over_yaml(monkeypatch, base_config):
    monkeypatch.setenv("MAX_DAILY_LOSS", "9.0")
    bot = TradingBot()
    bot.config = base_config
    bot.config["risk"]["max_daily_loss"] = 8.0

    bot._merge_config({"risk": {"max_daily_loss": 2.0}})

    assert bot.config["risk"]["max_daily_loss"] == 8.0


def test_yaml_risk_capital_base_applies_when_env_not_set(monkeypatch, base_config):
    monkeypatch.delenv("RISK_CAPITAL_BASE", raising=False)
    bot = TradingBot()
    bot.config = base_config

    bot._merge_config({"risk": {"capital_base": 10000.0}})

    assert bot.config["risk"]["capital_base"] == 10000.0
