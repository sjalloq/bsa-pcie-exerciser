#
# USB TLP Monitor - FIFO with Width Conversion
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Synchronous FIFO wrapper for monitor data path.
# Provides buffering and width conversion from N-bit to 32-bit.
# CDC to USB clock domain is handled by FT601 PHY.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.interconnect.stream import SyncFIFO, Converter


class MonitorFIFO(LiteXModule):
    """
    Synchronous FIFO with width conversion for monitor streaming.

    Write side: N-bit data
    Read side: 32-bit data

    Uses LiteX SyncFIFO followed by stream.Converter.

    Parameters
    ----------
    data_width : int
        Input data width. Default 64.

    depth : int
        FIFO depth in input words. Default 512.
    """

    def __init__(self, data_width=64, depth=512):
        # Write interface (N-bit)
        self.sink = stream.Endpoint([("data", data_width)])

        # Read interface (32-bit)
        self.source = stream.Endpoint([("data", 32)])

        # # #

        # Synchronous FIFO for buffering
        self.fifo = fifo = SyncFIFO(
            layout=[("data", data_width)],
            depth=depth,
            buffered=False,
        )

        # Width converter (N→32)
        self.converter = converter = Converter(
            nbits_from=data_width,
            nbits_to=32,
            reverse=False,
        )

        # Connect: sink → FIFO → converter → source
        self.comb += [
            self.sink.connect(fifo.sink),
            fifo.source.connect(converter.sink),
            converter.source.connect(self.source),
        ]


# Convenience aliases with sensible defaults
def MonitorHeaderFIFO(depth=4, **kwargs):
    """Header FIFO: 256-bit input, 32-bit output, 4 entries."""
    return MonitorFIFO(data_width=256, depth=depth)


def MonitorPayloadFIFO(depth=512, **kwargs):
    """Payload FIFO: 64-bit input, 32-bit output, 512 entries."""
    return MonitorFIFO(data_width=64, depth=depth)
