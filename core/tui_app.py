import asyncio
from datetime import datetime
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, RichLog
from textual.containers import Container, Horizontal, Vertical
from textual.binding import Binding
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

class ControlWidget(Static):
    """Widget for control status and simulation time."""
    def __init__(self, backtester, app_ptr, **kwargs):
        super().__init__(**kwargs)
        self.backtester = backtester
        self.app_ptr = app_ptr

    def on_mount(self):
        self.set_interval(0.1, self.update_info)

    def update_info(self):
        status = "[bold red]PAUSED[/]" if self.app_ptr.paused else "[bold green]RUNNING[/]"
        
        # Get time from current candle if available
        sim_time = "N/A"
        if self.backtester.current_index < len(self.backtester.data):
            candle = self.backtester.data.iloc[self.backtester.current_index]
            sim_time = candle["time"].strftime("%Y.%m.%d %H:%M")
        
        content = f"Status: {status}  |  Sim Time: [bold cyan]{sim_time}[/]"
        self.update(Panel(content, border_style="blue"))

class BacktestInfoWidget(Static):
    """Widget for backtest metadata."""
    def __init__(self, backtester, **kwargs):
        super().__init__(**kwargs)
        self.backtester = backtester

    def on_mount(self):
        self.refresh_info()

    def refresh_info(self):
        table = Table.grid(expand=True)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="bold white")
        
        table.add_row("Symbol", self.backtester.symbol)
        table.add_row("Timeframe", self.backtester.config.get("timeframe", "N/A"))
        table.add_row("Strategy", str(self.backtester.executor.__class__.__name__))
        table.add_row("Warmup", str(self.backtester.warmup))
        
        self.update(Panel(table, title="Backtest Info", border_style="yellow"))

class StatsWidget(Static):
    """Widget for account stats."""
    def __init__(self, backtester, **kwargs):
        super().__init__(**kwargs)
        self.backtester = backtester

    def on_mount(self):
        self.set_interval(0.5, self.update_stats)

    def update_stats(self):
        table = Table.grid(expand=True)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="bold white")
        
        profit = self.backtester.equity - self.backtester.initial_balance
        profit_color = "green" if profit >= 0 else "red"
        
        table.add_row("Balance", f"${self.backtester.balance:,.2f}")
        table.add_row("Equity", f"${self.backtester.equity:,.2f}")
        table.add_row("Profit ($)", Text(f"${profit:,.2f}", style=profit_color))
        table.add_row("Trades", str(len(self.backtester.trades)))
        
        self.update(Panel(table, title="Account Info", border_style="green"))

class BacktestTUI(App):
    """TUI for running a real backtest."""
    CSS = """
    Screen {
        background: #121212;
    }
    #main-layout {
        layout: grid;
        grid-size: 2 1;
        grid-columns: 1fr 1fr;
    }
    #left-column {
        height: 100%;
        padding: 0 1;
    }
    #right-column {
        height: 100%;
        padding: 0 1;
    }
    #controls {
        height: 5;
        margin-bottom: 1;
    }
    #info-section {
        layout: horizontal;
        height: 10;
        margin-bottom: 1;
    }
    #info-section Static {
        width: 50%;
    }
    #signal-history {
        height: 1fr;
        border: solid cyan;
    }
    DataTable {
        height: auto;
        max-height: 100%;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("r", "reload_config", "Restart"),
    ]

    def __init__(
        self,
        backtester,
        steps_per_tick: int = 20,
        update_interval: float = 0.05,
        reload_callback=None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.backtester = backtester
        self.paused = False
        self.last_pos_count = 0
        self.last_trade_count = 0
        self.steps_per_tick = max(1, int(steps_per_tick))
        self.update_interval = max(0.01, float(update_interval))
        self.reload_callback = reload_callback
        self.finished_logged = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield ControlWidget(self.backtester, self, id="controls")
        yield Container(
            Vertical(
                Horizontal(
                    BacktestInfoWidget(self.backtester, id="backtest-info"),
                    StatsWidget(self.backtester, id="stats-widget"),
                    id="info-section"
                ),
                DataTable(id="positions-table"),
                id="left-column"
            ),
            Vertical(
                RichLog(id="signal-history"),
                id="right-column"
            ),
            id="main-layout"
        )
        yield Footer()

    def on_mount(self):
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("ID", "Type", "Lot", "Entry", "SL", "TP", "PnL ($)")
        
        sig_log = self.query_one("#signal-history", RichLog)
        sig_log.write(Text("Backtest Simulation Started..."))
        
        self.set_interval(self.update_interval, self.tick)

    def _sync_backtester_refs(self):
        self.query_one("#controls", ControlWidget).backtester = self.backtester
        info = self.query_one("#backtest-info", BacktestInfoWidget)
        info.backtester = self.backtester
        info.refresh_info()
        self.query_one("#stats-widget", StatsWidget).backtester = self.backtester

    @staticmethod
    def _format_lot(lot: float) -> str:
        val = float(lot)
        if val >= 0.01:
            return f"{val:.2f}"
        if val <= 0:
            return "0"
        # For micro lots in backtest mode
        return f"{val:.6f}".rstrip("0").rstrip(".")

    def tick(self):
        if self.paused:
            return

        state = None
        opened_events = []
        closed_events = []
        for _ in range(self.steps_per_tick):
            state = self.backtester.step()
            if state is None:
                break
            opened_events.extend(state.get("opened_positions", []))
            closed_events.extend(state.get("closed_trades", []))

        if state is None:
            sig_log = self.query_one("#signal-history", RichLog)
            # Only log once
            if not getattr(self, "finished_logged", False):
                sig_log.write(Text(f"[{datetime.now().strftime('%Y.%m.%d %H:%M')}] [SYSTEM] Backtest Completed."))
                self.finished_logged = True
            return

        # Update log
        sig_log = self.query_one("#signal-history", RichLog)
        sim_time_str = state["candle"]["time"].strftime("%Y.%m.%d %H:%M")
        
        # Log event stream from the engine (covers open/close within same frame)
        for pos in opened_events:
            entry_time = pos.get("entry_time")
            if hasattr(entry_time, "strftime"):
                entry_time_str = entry_time.strftime("%Y.%m.%d %H:%M")
            else:
                entry_time_str = sim_time_str
            side_name = pos["signal"].name
            side_text = Text(side_name, style="green" if side_name == "BUY" else "red")
            line = Text(f"[{entry_time_str}] OPEN ")
            line.append_text(side_text)
            line.append(f" at {pos['entry_price']:.2f} (Lot: {self._format_lot(pos['lot_size'])})")
            sig_log.write(line)
        for trade in closed_events:
            trade_time = trade.get("exit_time")
            if hasattr(trade_time, "strftime"):
                trade_time_str = trade_time.strftime("%Y.%m.%d %H:%M")
            else:
                trade_time_str = sim_time_str
            side_text = Text(trade["signal"], style="green" if trade["signal"] == "BUY" else "red")
            line = Text(f"[{trade_time_str}] CLOSE ")
            line.append_text(side_text)
            line.append(f" at {trade['exit_price']:.2f} (Profit: ${trade['profit']:.2f})")
            sig_log.write(line)

        self.last_pos_count = len(self.backtester.positions)
        self.last_trade_count = len(self.backtester.trades)
        
        # Update Table
        table = self.query_one("#positions-table", DataTable)
        table.clear()
        for i, pos in enumerate(self.backtester.positions):
            # Calculate current PnL for display
            candle = state["candle"]
            is_buy = pos["signal"].name == "BUY"
            pips = (candle["close"] - pos["entry_price"]) / self.backtester.point if is_buy else (pos["entry_price"] - candle["close"]) / self.backtester.point
            multiplier = 100 if "XAU" in self.backtester.symbol else 100000
            pnl = (pips * self.backtester.point) * pos["lot_size"] * multiplier
            
            pnl_style = "green" if pnl >= 0 else "red"
            
            table.add_row(
                str(i),
                Text(pos["signal"].name, style="green" if pos["signal"].name == "BUY" else "red"),
                self._format_lot(pos["lot_size"]),
                f"{pos['entry_price']:.2f}",
                f"{pos['stop_loss']:.2f}",
                f"{pos['take_profit']:.2f}",
                Text(f"${pnl:.2f}", style=pnl_style)
            )

    def action_toggle_pause(self):
        self.paused = not self.paused
        sig_log = self.query_one("#signal-history", RichLog)
        sig_log.write(Text("PAUSED" if self.paused else "RESUMED"))

    def action_reload_config(self):
        sig_log = self.query_one("#signal-history", RichLog)
        if self.reload_callback is None:
            sig_log.write(Text("Reload unavailable: no config callback provided."))
            return

        try:
            new_backtester, message = self.reload_callback()
            if new_backtester is None:
                sig_log.write(Text(f"Reload failed: {message}", style="red"))
                return

            self.backtester = new_backtester
            self.paused = False
            self.last_pos_count = 0
            self.last_trade_count = 0
            self.finished_logged = False
            self._sync_backtester_refs()

            table = self.query_one("#positions-table", DataTable)
            table.clear()
            sig_log.clear()
            sig_log.write(Text(message or "Config reloaded and simulation restarted.", style="green"))
        except Exception as e:
            sig_log.write(Text(f"Reload failed: {e}", style="red"))
