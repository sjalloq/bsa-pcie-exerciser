#
# BSA Monitor - Live TUI View
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Rich-based terminal UI for live transaction viewing.
#

import time
from collections import deque
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from bsa_pcie_exerciser.common.protocol import TLPPacket, TLPType, Direction
from .capture import MonitorCapture

# Alias for backwards compatibility
MonitorPacket = TLPPacket


def packet_type_style(pkt_type: int) -> str:
    """Get Rich style for packet type."""
    styles = {
        TLPType.MRD: "cyan",
        TLPType.MWR: "green",
        TLPType.CPL: "yellow",
        TLPType.CPLD: "yellow",
        TLPType.MSIX: "magenta",
        TLPType.ATS_REQ: "red",
        TLPType.ATS_CPL: "red dim",
        TLPType.ATS_INV: "red bold",
    }
    return styles.get(pkt_type, "white")


def format_address(addr: int) -> str:
    """Format address with highlighting for BAR regions."""
    if addr < 0x1000:
        return f"[bold cyan]0x{addr:08x}[/]"  # BAR0: CSRs
    elif addr < 0x5000:
        return f"[bold green]0x{addr:08x}[/]"  # BAR1: DMA
    elif addr < 0xD000:
        return f"[bold magenta]0x{addr:08x}[/]"  # BAR2: MSI-X
    else:
        return f"0x{addr:016x}"


class LiveView:
    """
    Rich-based live view of monitor packets.

    Shows a scrolling table of recent transactions with statistics.
    """

    def __init__(
        self,
        max_rows: int = 30,
        show_stats: bool = True,
    ):
        """
        Initialize live view.

        Args:
            max_rows: Maximum rows to display in table
            show_stats: Whether to show statistics panel
        """
        self.max_rows = max_rows
        self.show_stats = show_stats
        self.console = Console()
        self._packets: deque[MonitorPacket] = deque(maxlen=max_rows)
        self._capture: Optional[MonitorCapture] = None
        self._running = False

        # Statistics
        self._stats = {
            'total': 0,
            'reads': 0,
            'writes': 0,
            'completions': 0,
            'overflows': 0,
        }

    def _update_stats(self, pkt: MonitorPacket) -> None:
        """Update statistics for a packet."""
        self._stats['total'] += 1
        if pkt.is_read:
            self._stats['reads'] += 1
        elif pkt.is_write:
            self._stats['writes'] += 1
        elif pkt.is_completion:
            self._stats['completions'] += 1

    def _make_table(self) -> Table:
        """Create the packets table."""
        table = Table(
            title="PCIe Transactions",
            show_header=True,
            header_style="bold",
            border_style="dim",
            expand=True,
        )

        table.add_column("Seq", style="dim", width=8, justify="right")
        table.add_column("Time (us)", width=12, justify="right")
        table.add_column("Dir", width=3, justify="center")
        table.add_column("Type", width=12)
        table.add_column("Address", width=18)
        table.add_column("Data", width=18)
        table.add_column("Tag", width=4, justify="center")
        table.add_column("BE", width=5, justify="center")

        for pkt in self._packets:
            style = packet_type_style(pkt.tlp_type)
            direction = "[cyan]<-[/]" if pkt.direction == Direction.RX else "[yellow]->[/]"

            # Format payload preview (first 8 bytes if available)
            if pkt.payload:
                payload_preview = pkt.payload[:8].hex()
                if len(pkt.payload) > 8:
                    payload_preview += "..."
            else:
                payload_preview = "-"

            table.add_row(
                str(pkt.header_count),
                f"{pkt.timestamp_us:.3f}",
                direction,
                Text(pkt.type_name, style=style),
                format_address(pkt.address),
                payload_preview,
                f"{pkt.tag:02x}",
                f"{pkt.first_be:x}/{pkt.last_be:x}",
            )

        return table

    def _make_stats_panel(self) -> Panel:
        """Create the statistics panel."""
        if not self._capture:
            return Panel("No capture")

        elapsed = time.time() - self._capture.stats['start_time']
        rate = self._capture.packet_rate

        text = Text()
        text.append(f"Packets: {self._stats['total']:,}\n", style="bold")
        text.append(f"  Reads:       {self._stats['reads']:,}\n", style="cyan")
        text.append(f"  Writes:      {self._stats['writes']:,}\n", style="green")
        text.append(f"  Completions: {self._stats['completions']:,}\n", style="yellow")
        if self._stats['overflows'] > 0:
            text.append(f"  Overflows:   {self._stats['overflows']:,}\n", style="red bold")
        text.append(f"\nRate: {rate:.1f} pkt/s\n", style="dim")
        text.append(f"Time: {elapsed:.1f}s", style="dim")

        return Panel(text, title="Statistics", border_style="dim")

    def _make_layout(self) -> Layout:
        """Create the display layout."""
        layout = Layout()

        if self.show_stats:
            layout.split_row(
                Layout(name="table", ratio=4),
                Layout(name="stats", ratio=1),
            )
            layout["table"].update(self._make_table())
            layout["stats"].update(self._make_stats_panel())
        else:
            layout.update(self._make_table())

        return layout

    def _on_packet(self, pkt: MonitorPacket) -> None:
        """Callback for received packets."""
        self._packets.append(pkt)
        self._update_stats(pkt)

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 2345,
        refresh_rate: float = 4.0,
    ) -> None:
        """
        Run the live view.

        Args:
            host: UDP host to connect to
            port: UDP port to listen on
            refresh_rate: Display refresh rate (Hz)
        """
        self._capture = MonitorCapture(
            host=host,
            port=port,
            callback=self._on_packet,
        )

        self.console.print(f"[bold]BSA Monitor[/] - Listening on {host}:{port}")
        self.console.print("Press Ctrl+C to stop\n")

        self._capture.start()
        self._running = True

        try:
            with Live(
                self._make_layout(),
                console=self.console,
                refresh_per_second=refresh_rate,
                screen=True,
            ) as live:
                while self._running:
                    live.update(self._make_layout())
                    time.sleep(1.0 / refresh_rate)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self._capture.stop()
            self.console.print("\n[dim]Capture stopped[/]")


def run_live_view(
    host: str = "127.0.0.1",
    port: int = 2345,
    max_rows: int = 30,
    no_stats: bool = False,
) -> None:
    """
    Entry point for live view.

    Args:
        host: UDP host
        port: UDP port
        max_rows: Maximum table rows
        no_stats: Disable statistics panel
    """
    view = LiveView(max_rows=max_rows, show_stats=not no_stats)
    view.run(host=host, port=port)
