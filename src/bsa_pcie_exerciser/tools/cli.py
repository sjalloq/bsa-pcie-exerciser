#
# BSA Monitor - Command Line Interface
#
# Copyright (c) 2025 Shareef Jalloq <shareef@jalloq.co.uk>
# SPDX-License-Identifier: BSD-2-Clause
#
# Click-based CLI for the BSA monitor tools.
#

import sys
import time
from pathlib import Path

import click

from .capture import MonitorCapture, FileCapture, DEFAULT_MONITOR_PORT
from .export import convert_capture


@click.group()
@click.version_option(version='0.1.0')
def cli():
    """BSA PCIe Exerciser - Transaction Monitor Tools.

    Monitor PCIe transactions from the BSA Exerciser via USB.

    Prerequisites:
      1. FPGA programmed with Squirrel bitstream
      2. usb2udp daemon running
      3. Transaction monitoring enabled via CSR
    """
    pass


@cli.command()
@click.option('-h', '--host', default='127.0.0.1', help='UDP host')
@click.option('-p', '--port', default=DEFAULT_MONITOR_PORT, help='UDP port')
@click.option('-r', '--rows', default=30, help='Max table rows')
@click.option('--no-stats', is_flag=True, help='Hide statistics panel')
def live(host: str, port: int, rows: int, no_stats: bool):
    """Live view of PCIe transactions.

    Shows a scrolling table of transactions with real-time statistics.
    Press Ctrl+C to stop.
    """
    from .live import run_live_view

    run_live_view(
        host=host,
        port=port,
        max_rows=rows,
        no_stats=no_stats,
    )


@cli.command()
@click.option('-h', '--host', default='127.0.0.1', help='UDP host')
@click.option('-p', '--port', default=DEFAULT_MONITOR_PORT, help='UDP port')
@click.option('-o', '--output', required=True, type=click.Path(), help='Output file (.bsax)')
@click.option('-n', '--count', default=0, help='Stop after N packets (0=unlimited)')
@click.option('-t', '--time', 'duration', default=0.0, help='Stop after N seconds (0=unlimited)')
@click.option('-q', '--quiet', is_flag=True, help='Suppress progress output')
def capture(host: str, port: int, output: str, count: int, duration: float, quiet: bool):
    """Capture transactions to a file.

    Captures monitor packets from UDP and writes them to a binary file
    for later analysis.

    Example:
      bsa-monitor capture -o trace.bsax -t 10
    """
    output_path = Path(output)
    if not output_path.suffix:
        output_path = output_path.with_suffix('.bsax')

    cap = MonitorCapture(host=host, port=port)

    if not quiet:
        click.echo(f"Capturing to {output_path}")
        click.echo(f"Listening on {host}:{port}")
        if count > 0:
            click.echo(f"Will stop after {count} packets")
        if duration > 0:
            click.echo(f"Will stop after {duration:.1f} seconds")
        click.echo("Press Ctrl+C to stop\n")

    cap.start()
    start_time = time.time()
    packets_written = 0

    try:
        with FileCapture(str(output_path)) as fc:
            while True:
                # Check stop conditions
                if count > 0 and packets_written >= count:
                    break
                if duration > 0 and (time.time() - start_time) >= duration:
                    break

                # Get and write packets
                pkt = cap.get_packet(timeout=0.1)
                if pkt:
                    fc.write(pkt)
                    packets_written += 1

                    if not quiet and packets_written % 100 == 0:
                        elapsed = time.time() - start_time
                        rate = packets_written / elapsed if elapsed > 0 else 0
                        click.echo(f"\rPackets: {packets_written:,}  Rate: {rate:.1f}/s  ", nl=False)

    except KeyboardInterrupt:
        pass
    finally:
        cap.stop()

    if not quiet:
        click.echo(f"\n\nCaptured {packets_written:,} packets to {output_path}")


@cli.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('-o', '--output', required=True, type=click.Path(), help='Output file')
@click.option('-f', '--format', 'fmt', default='auto',
              type=click.Choice(['auto', 'json', 'jsonl', 'csv', 'txt']),
              help='Output format')
def export(input_file: str, output: str, fmt: str):
    """Export capture file to another format.

    Converts a .bsax capture file to JSON, CSV, or text format.

    Example:
      bsa-monitor export trace.bsax -o trace.jsonl
    """
    input_path = Path(input_file)
    output_path = Path(output)

    click.echo(f"Converting {input_path} -> {output_path}")

    count = convert_capture(input_path, output_path, format=fmt)

    click.echo(f"Exported {count:,} packets")


@cli.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('-n', '--count', default=20, help='Number of packets to show')
@click.option('--head', is_flag=True, help='Show first N packets')
@click.option('--tail', is_flag=True, help='Show last N packets')
def show(input_file: str, count: int, head: bool, tail: bool):
    """Show packets from a capture file.

    Displays packets in human-readable format.

    Example:
      bsa-monitor show trace.bsax --head -n 10
    """
    from .capture import read_capture_file

    input_path = Path(input_file)
    packets = list(read_capture_file(input_path))

    if not packets:
        click.echo("No packets in file")
        return

    if tail and not head:
        packets = packets[-count:]
    elif head or not tail:
        packets = packets[:count]

    click.echo(f"File: {input_path}")
    click.echo(f"Total packets: {len(packets)}\n")
    click.echo("-" * 80)

    for pkt in packets:
        click.echo(str(pkt))

    click.echo("-" * 80)


@cli.command()
@click.argument('input_file', type=click.Path(exists=True))
def stats(input_file: str):
    """Show statistics for a capture file.

    Example:
      bsa-monitor stats trace.bsax
    """
    from .capture import read_capture_file
    from bsa_pcie_exerciser.common.protocol import TLPType, Direction

    input_path = Path(input_file)
    packets = list(read_capture_file(input_path))

    if not packets:
        click.echo("No packets in file")
        return

    # Calculate statistics
    type_counts = {}
    inbound = 0
    outbound = 0
    first_ts = packets[0].timestamp
    last_ts = packets[-1].timestamp

    for pkt in packets:
        type_name = pkt.type_name
        type_counts[type_name] = type_counts.get(type_name, 0) + 1
        if pkt.direction == Direction.RX:
            inbound += 1
        else:
            outbound += 1

    duration_us = (last_ts - first_ts) / 1000.0
    duration_ms = duration_us / 1000.0

    click.echo(f"File: {input_path}")
    click.echo(f"Total packets: {len(packets):,}")
    click.echo(f"Duration: {duration_ms:.3f} ms ({duration_us:.1f} us)")
    click.echo(f"Average rate: {len(packets) / (duration_ms / 1000):.1f} pkt/s" if duration_ms > 0 else "")
    click.echo()
    click.echo("Direction:")
    click.echo(f"  Inbound:  {inbound:,}")
    click.echo(f"  Outbound: {outbound:,}")
    click.echo()
    click.echo("Packet types:")
    for type_name, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        click.echo(f"  {type_name:15s}: {count:,}")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()
