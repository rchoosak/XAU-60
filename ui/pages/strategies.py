"""
Strategies Page - Manage trading strategies.
"""
import streamlit as st
import pandas as pd
import yaml
import locale
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _load_yaml_file(path: Path) -> dict:
    """Load YAML with cross-platform encoding fallbacks."""
    encodings = ["utf-8-sig", "utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower() not in {e.lower() for e in encodings}:
        encodings.append(preferred)
    for fallback in ("cp1252", "cp874", "latin-1"):
        if fallback.lower() not in {e.lower() for e in encodings}:
            encodings.append(fallback)

    last_error: Optional[Exception] = None
    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                return yaml.safe_load(f) or {}
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return yaml.safe_load(f) or {}


def _save_yaml_file(path: Path, payload: dict) -> None:
    """Persist YAML in UTF-8 so all platforms can read it consistently."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def render_strategies():
    """Render the strategies management page."""
    st.title("🎯 Strategy Management")

    # Strategy tabs
    tab1, tab2, tab3 = st.tabs(["Active Strategies", "Configure", "Add New"])

    with tab1:
        render_active_strategies()

    with tab2:
        render_strategy_config()

    with tab3:
        render_add_strategy()


def render_active_strategies():
    """Render the list of active strategies."""
    st.subheader("Active Strategies")

    # Load strategy configs
    config_dir = Path(__file__).parent.parent.parent / "config" / "strategies"

    strategies = []
    if config_dir.exists():
        for config_file in config_dir.glob("*.yaml"):
            try:
                config = _load_yaml_file(config_file)
                strategies.append({
                    "file": config_file.name,
                    **config
                })
            except Exception as e:
                st.error(f"Error loading {config_file.name}: {e}")

    if not strategies:
        st.info("No strategies found. Add a new strategy to get started.")
        return

    for strategy in strategies:
        with st.expander(f"**{strategy.get('name', 'Unknown')}** - {strategy.get('timeframe', 'N/A')}", expanded=True):
            col1, col2, col3 = st.columns([2, 2, 1])

            with col1:
                st.markdown(f"**Description:** {strategy.get('description', 'N/A')}")
                st.markdown(f"**Symbols:** {', '.join(strategy.get('symbols', []))}")
                st.markdown(f"**Timeframe:** {strategy.get('timeframe', 'N/A')}")

            with col2:
                params = strategy.get('parameters', {})
                st.markdown("**Key Parameters:**")
                for key, value in list(params.items())[:4]:
                    st.markdown(f"- {key}: `{value}`")

            with col3:
                enabled = strategy.get('enabled', False)
                if enabled:
                    st.success("ENABLED")
                else:
                    st.error("DISABLED")

                if st.button("Toggle", key=f"toggle_{strategy['file']}"):
                    toggle_strategy(config_dir / strategy['file'], not enabled)
                    st.rerun()


def render_strategy_config():
    """Render strategy configuration editor."""
    st.subheader("Configure Strategy")

    config_dir = Path(__file__).parent.parent.parent / "config" / "strategies"
    config_files = list(config_dir.glob("*.yaml")) if config_dir.exists() else []

    if not config_files:
        st.info("No strategies to configure.")
        return

    selected_file = st.selectbox(
        "Select Strategy",
        options=config_files,
        format_func=lambda x: x.stem.replace("_", " ").title()
    )

    if selected_file:
        try:
            config = _load_yaml_file(selected_file)

            st.markdown("---")

            # Basic settings
            col1, col2 = st.columns(2)
            timeframe_options = ["S1", "M1", "M5", "M15", "M30", "H1", "H4", "D1"]
            current_timeframe = config.get("timeframe", "M15")
            timeframe_index = (
                timeframe_options.index(current_timeframe)
                if current_timeframe in timeframe_options
                else timeframe_options.index("M15")
            )

            with col1:
                new_name = st.text_input("Strategy Name", value=config.get('name', ''))
                new_enabled = st.checkbox("Enabled", value=config.get('enabled', False))
                new_timeframe = st.selectbox(
                    "Timeframe",
                    options=timeframe_options,
                    index=timeframe_index,
                )

            with col2:
                new_symbols = st.text_input(
                    "Symbols (comma-separated)",
                    value=", ".join(config.get('symbols', []))
                )
                new_magic = st.number_input(
                    "Magic Number",
                    value=config.get('magic_number', 123456),
                    step=1
                )

            st.markdown("---")
            st.markdown("### Parameters")

            params = config.get('parameters', {})
            new_params = {}

            cols = st.columns(3)
            for i, (key, value) in enumerate(params.items()):
                with cols[i % 3]:
                    if isinstance(value, bool):
                        new_params[key] = st.checkbox(key, value=value)
                    elif isinstance(value, float):
                        new_params[key] = st.number_input(key, value=value, format="%.2f")
                    elif isinstance(value, int):
                        new_params[key] = st.number_input(key, value=value, step=1)
                    else:
                        new_params[key] = st.text_input(key, value=str(value))

            st.markdown("---")

            if st.button("Save Configuration", type="primary"):
                # Update config
                config['name'] = new_name
                config['enabled'] = new_enabled
                config['timeframe'] = new_timeframe
                config['symbols'] = [s.strip() for s in new_symbols.split(",")]
                config['magic_number'] = int(new_magic)
                config['parameters'] = new_params

                _save_yaml_file(selected_file, config)

                st.success("Configuration saved!")
                st.rerun()

        except Exception as e:
            st.error(f"Error loading configuration: {e}")


def render_add_strategy():
    """Render form to add a new strategy."""
    st.subheader("Add New Strategy")

    st.info("""
    To add a new strategy:
    1. Create a Python file in `strategies/` folder that inherits from `StrategyBase`
    2. Implement the required methods: `initialize()`, `analyze()`, `should_close()`
    3. Create a YAML configuration file below
    """)

    st.markdown("---")

    # Strategy template
    st.markdown("### Quick Start Template")

    template_code = '''"""
My Custom Strategy
"""
from core.strategy_base import StrategyBase, Signal, TradeSignal, Position
import pandas as pd
from typing import Optional, Dict, Any

class MyStrategy(StrategyBase):
    name = "My Strategy"
    version = "1.0.0"
    description = "My custom trading strategy"

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M15")
        self.enabled = config.get("enabled", True)
        # Load your parameters here

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        # Implement your entry logic here
        # Return TradeSignal for entry, None otherwise
        return None

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        # Implement your exit logic here
        return False
'''

    st.code(template_code, language="python")

    st.markdown("---")

    # Create config file
    st.markdown("### Create Configuration File")

    new_name = st.text_input("Strategy Name", placeholder="My Strategy")
    new_file = st.text_input("File Name (without .yaml)", placeholder="my_strategy")
    new_symbols = st.text_input("Symbols", value="XAUUSD")
    new_timeframe = st.selectbox("Timeframe", ["S1", "M1", "M5", "M15", "M30", "H1", "H4", "D1"], index=3)

    if st.button("Create Strategy Config", type="primary"):
        if not new_name or not new_file:
            st.error("Please fill in all required fields.")
        else:
            config = {
                "name": new_name,
                "enabled": False,
                "description": "My custom strategy",
                "symbols": [s.strip() for s in new_symbols.split(",")],
                "timeframe": new_timeframe,
                "magic_number": 123456,
                "parameters": {
                    "param1": 10,
                    "param2": 2.0,
                },
                "risk": {
                    "max_risk_percent": 2.0,
                    "lot_size": 0.01
                }
            }

            config_dir = Path(__file__).parent.parent.parent / "config" / "strategies"
            config_dir.mkdir(parents=True, exist_ok=True)

            config_path = config_dir / f"{new_file}.yaml"
            _save_yaml_file(config_path, config)

            st.success(f"Created {config_path}")
            st.info(f"Now create `strategies/{new_file}.py` with your strategy implementation.")


def toggle_strategy(config_path: Path, enabled: bool):
    """Toggle strategy enabled/disabled."""
    try:
        config = _load_yaml_file(config_path)

        config['enabled'] = enabled

        _save_yaml_file(config_path, config)

    except Exception as e:
        st.error(f"Error toggling strategy: {e}")
