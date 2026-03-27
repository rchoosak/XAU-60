import sys
import subprocess

import main as main_module


def test_ui_mode_launches_dashboard_only(monkeypatch):
    run_calls = []
    bot_created = []

    class FakeBot:
        def __init__(self, *_args, **_kwargs):
            bot_created.append(True)

    monkeypatch.setattr(main_module, "TradingBot", FakeBot)
    monkeypatch.setattr(subprocess, "run", lambda cmd: run_calls.append(cmd))
    monkeypatch.setattr(sys, "argv", ["main.py", "--ui"])

    main_module.main()

    assert run_calls == [["streamlit", "run", "ui/app.py"]]
    assert bot_created == []


def test_ui_with_bot_launches_dashboard_and_starts_bot(monkeypatch):
    run_calls = []
    bot_instances = []
    thread_instances = []

    class FakeBot:
        def __init__(self, config_path):
            self.config_path = config_path
            self.run_called = False
            self.running = True
            bot_instances.append(self)

        def run(self):
            self.run_called = True

    class FakeThread:
        def __init__(self, target, name=None, daemon=None):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.join_called = False
            self._alive = False
            thread_instances.append(self)

        def start(self):
            self.target()
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            self.join_called = True
            self._alive = False

    monkeypatch.setattr(main_module, "TradingBot", FakeBot)
    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(subprocess, "run", lambda cmd: run_calls.append(cmd))
    monkeypatch.setattr(sys, "argv", ["main.py", "--ui-with-bot", "--config", "custom.yaml"])

    main_module.main()

    assert len(bot_instances) == 1
    assert bot_instances[0].config_path == "custom.yaml"
    assert bot_instances[0].run_called is True
    assert bot_instances[0].running is False
    assert len(thread_instances) == 1
    assert thread_instances[0].join_called is True
    assert run_calls == [["streamlit", "run", "ui/app.py"]]


def test_default_mode_runs_bot_loop(monkeypatch):
    events = []

    class FakeBot:
        def __init__(self, config_path):
            events.append(("init", config_path))

        def dry_run(self):
            events.append(("dry_run",))

        def run(self):
            events.append(("run",))

    monkeypatch.setattr(main_module, "TradingBot", FakeBot)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    main_module.main()

    assert ("init", "config/settings.yaml") in events
    assert ("run",) in events
    assert ("dry_run",) not in events
