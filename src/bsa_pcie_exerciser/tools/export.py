#
# BSA Monitor - Export Functions
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Export captured packets to various formats.
#

import json
from pathlib import Path
from typing import Iterable

from bsa_pcie_exerciser.common.protocol import TLPPacket, packet_to_dict
from .capture import read_capture_file

# Alias for backwards compatibility
MonitorPacket = TLPPacket


def export_jsonl(
    packets: Iterable[MonitorPacket],
    output: Path,
    pretty: bool = False,
) -> int:
    """
    Export packets to JSON Lines format.

    Each packet is written as a single JSON object per line.

    Args:
        packets: Iterable of packets to export
        output: Output file path
        pretty: If True, pretty-print each JSON object

    Returns:
        Number of packets exported
    """
    count = 0
    indent = 2 if pretty else None

    with open(output, 'w') as f:
        for pkt in packets:
            obj = packet_to_dict(pkt)
            line = json.dumps(obj, indent=indent)
            f.write(line + '\n')
            count += 1

    return count


def export_json(
    packets: Iterable[MonitorPacket],
    output: Path,
) -> int:
    """
    Export packets to JSON array format.

    All packets are written as a single JSON array.

    Args:
        packets: Iterable of packets to export
        output: Output file path

    Returns:
        Number of packets exported
    """
    packet_list = [packet_to_dict(pkt) for pkt in packets]

    with open(output, 'w') as f:
        json.dump(packet_list, f, indent=2)

    return len(packet_list)


def export_csv(
    packets: Iterable[MonitorPacket],
    output: Path,
) -> int:
    """
    Export packets to CSV format.

    Args:
        packets: Iterable of packets to export
        output: Output file path

    Returns:
        Number of packets exported
    """
    import csv

    fieldnames = [
        'timestamp_us', 'type', 'direction', 'bar_hit',
        'address', 'payload_length', 'first_be', 'last_be', 'attr', 'at',
        'req_id', 'tag', 'status', 'cmp_id', 'byte_count',
    ]

    count = 0
    with open(output, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for pkt in packets:
            row = {
                'timestamp_us': pkt.timestamp_us,
                'type': pkt.type_name,
                'direction': 'rx' if pkt.direction == 0 else 'tx',
                'bar_hit': pkt.bar_hit,
                'address': f"0x{pkt.address:016x}",
                'payload_length': pkt.payload_length,
                'first_be': pkt.first_be,
                'last_be': pkt.last_be,
                'attr': pkt.attr,
                'at': pkt.at,
                'req_id': f"0x{pkt.req_id:04x}",
                'tag': pkt.tag,
                'status': pkt.status if pkt.is_completion else '',
                'cmp_id': f"0x{pkt.cmp_id:04x}" if pkt.is_completion else '',
                'byte_count': pkt.byte_count if pkt.is_completion else '',
            }
            writer.writerow(row)
            count += 1

    return count


def export_text(
    packets: Iterable[MonitorPacket],
    output: Path,
) -> int:
    """
    Export packets to human-readable text format.

    Args:
        packets: Iterable of packets to export
        output: Output file path

    Returns:
        Number of packets exported
    """
    count = 0
    with open(output, 'w') as f:
        f.write("BSA PCIe Transaction Trace\n")
        f.write("=" * 80 + "\n\n")

        for pkt in packets:
            f.write(str(pkt) + "\n")
            count += 1

        f.write("\n" + "=" * 80 + "\n")
        f.write(f"Total packets: {count}\n")

    return count


def convert_capture(
    input_file: Path,
    output_file: Path,
    format: str = 'auto',
) -> int:
    """
    Convert capture file to another format.

    Args:
        input_file: Input capture file (.bsax)
        output_file: Output file path
        format: Output format ('json', 'jsonl', 'csv', 'txt', or 'auto')

    Returns:
        Number of packets converted
    """
    # Determine format from extension if auto
    if format == 'auto':
        suffix = output_file.suffix.lower()
        format_map = {
            '.json': 'json',
            '.jsonl': 'jsonl',
            '.csv': 'csv',
            '.txt': 'txt',
            '.text': 'txt',
        }
        format = format_map.get(suffix, 'jsonl')

    # Read packets from capture file
    packets = list(read_capture_file(input_file))

    # Export to requested format
    exporters = {
        'json': export_json,
        'jsonl': export_jsonl,
        'csv': export_csv,
        'txt': export_text,
        'text': export_text,
    }

    exporter = exporters.get(format)
    if not exporter:
        raise ValueError(f"Unknown export format: {format}")

    return exporter(packets, output_file)
