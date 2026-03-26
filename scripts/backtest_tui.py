import asyncio
import os
import random
from datetime import datetime
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, Log
from textual.containers import Container, Horizontal, Vertical
from textual.binding import Binding
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from tui_logic import DataSimulator, PositionManager, SignalManager

class ControlWidget(Static):
    """Widget for control status and simulation time."""
    def __init__(self, simulator: DataSimulator, app_ptr, **kwargs):
        super().__init__(**kwargs)
        self.simulator = simulator
        self.app_ptr = app_ptr

    def on_mount(self):
        self.set_interval(0.1, self.update_info)

    def update_info(self):
        status = "[bold red]PAUSED[/]" if self.app_ptr.paused else "[bold green]RUNNING[/]"
        sim_time = self.simulator.current_sim_time.strftime("%Y.%m.%d %H:%M")
        
        content = f"Status: {status}  |  Sim Time: [bold cyan]{sim_time}[/]"
        self.update(Panel(content, border_style="blue"))

class BacktestInfoWidget(Static):
    """Widget for backtest metadata."""
    def on_mount(self):
        table = Table.grid(expand=True)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="bold white")
        
        table.add_row("Symbol", "XAUUSD")
        table.add_row("Timeframe", "M1")
        table.add_row("Strategy", "Mock Scalper")
        table.add_row("Start Date", "2024.03.26")
        
        self.update(Panel(table, title="Backtest Info", border_style="yellow"))

class StatsWidget(Static):
    """Widget for account stats."""
    def __init__(self, manager: PositionManager, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager

    def on_mount(self):
        self.set_interval(0.5, self.update_stats)

    def update_stats(self):
        table = Table.grid(expand=True)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="bold white")
        
        profit = self.manager.equity - self.manager.balance
        profit_color = "green" if profit >= 0 else "red"
        
        table.add_row("Balance", f"${self.manager.balance:,.2f}")
        table.add_row("Equity", f"${self.manager.equity:,.2f}")
        table.add_row("Profit ($)", Text(f"${profit:,.2f}", style=profit_color))
        table.add_row("Profit (%)", Text(f"{(profit/self.manager.balance)*100:.2f}%", style=profit_color))
        
        self.update(Panel(table, title="Account Info", border_style="green"))

class TradingApp(App):
    """The main TUI Application."""
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
        border-left: tall $accent;
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
    #positions-container {
        height: 1fr;
        border: solid blue;
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
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.simulator = DataSimulator()
        self.pos_manager = PositionManager()
        self.sig_manager = SignalManager()
        self.paused = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield ControlWidget(self.simulator, self, id="controls")
        yield Container(
            Vertical(
                Horizontal(
                    BacktestInfoWidget(),
                    StatsWidget(self.pos_manager),
                    id="info-section"
                ),
                DataTable(id="positions-table"),
                id="left-column"
            ),
            Vertical(
                Log(id="signal-history"),
                id="right-column"
            ),
            id="main-layout"
        )
        yield Footer()

    def on_mount(self):
        table = self.query_one("#positions-table", DataTable)
        table.add_columns("ID", "Type", "Lot", "Entry", "Current", "PnL (%)", "PnL ($)")
        
        sig_log = self.query_one("#signal-history", Log)
        sig_log.write_line("System Ready. Monitoring signals...")
        
        self.set_interval(1.0, self.tick)

    def tick(self):
        if self.paused:
            return

        candle = self.simulator.tick()
        self.pos_manager.update(candle["close"])
        
        # Signal generation
        if random.random() < 0.05:
            sig_type = random.choice(["BUY", "SELL"])
            lot = 0.1
            self.sig_manager.add_signal(sig_type, candle["close"])
            self.pos_manager.open_position(sig_type, candle["close"], lot=lot)
            
            sig_log = self.query_one("#signal-history", Log)
            sim_time_str = candle["time"].strftime("%Y.%m.%d %H:%M")
            sig_log.write_line(f"[{sim_time_str}] {sig_type} at {candle['close']:.2f}")

        # Update table
        table = self.query_one("#positions-table", DataTable)
        table.clear()
        for pos in self.pos_manager.open_positions:
            pnl_style = "green" if pos["pnl_usd"] >= 0 else "red"
            table.add_row(
                str(pos["id"]),
                pos["type"],
                f"{pos['lot']:.2f}",
                f"{pos['entry_price']:.2f}",
                f"{pos['current_price']:.2f}",
                Text(f"{pos['pnl_pct']}%", style=pnl_style),
                Text(f"${pos['pnl_usd']}", style=pnl_style)
            )

    def action_toggle_pause(self):
        self.paused = not self.paused
        sig_log = self.query_one("#signal-history", Log)
        sig_log.write_line("PAUSED" if self.paused else "RESUMED")

if __name__ == "__main__":
    app = TradingApp()
    app.run()
