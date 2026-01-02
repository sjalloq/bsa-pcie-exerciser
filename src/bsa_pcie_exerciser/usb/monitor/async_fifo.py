#
# USB TLP Monitor - Async FIFO with Width Conversion
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Async FIFO wrapper for monitor data path.
# Handles CDC from sys_clk to usb_clk with width conversion.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.interconnect.stream import ClockDomainCrossing, Converter


class MonitorAsyncFIFO(LiteXModule):
    """
    Async FIFO with width conversion for monitor streaming.

    Write side: N-bit data in write clock domain
    Read side: 32-bit data in read clock domain

    Uses LiteX ClockDomainCrossing (AsyncFIFO) followed by stream.Converter.

    Parameters
    ----------
    data_width : int
        Input data width. Default 64.

    depth : int
        FIFO depth in input words. Default 512.

    cd_write : str
        Write clock domain name. Default "sys".

    cd_read : str
        Read clock domain name. Default "usb".
    """

    def __init__(self, data_width=64, depth=512, cd_write="sys", cd_read="usb"):
        # Write interface (N-bit, write clock domain)
        self.sink = stream.Endpoint([("data", data_width)])

        # Read interface (32-bit, read clock domain)
        self.source = stream.Endpoint([("data", 32)])

        # # #

        # Async FIFO for CDC
        self.cdc = cdc = ClockDomainCrossing(
            layout=[("data", data_width)],
            cd_from=cd_write,
            cd_to=cd_read,
            depth=depth,
            buffered=True,
        )

        # Width converter (N→32, in read clock domain)
        converter = Converter(nbits_from=data_width, nbits_to=32, reverse=False)
        converter = ClockDomainsRenamer(cd_read)(converter)
        self.submodules.converter = converter

        # Connect: sink → CDC → converter → source
        self.comb += [
            self.sink.connect(cdc.sink),
            cdc.source.connect(converter.sink),
            converter.source.connect(self.source),
        ]


# Convenience aliases with sensible defaults
def MonitorHeaderFIFO(depth=4, cd_write="sys", cd_read="usb"):
    """Header FIFO: 256-bit input, 32-bit output, 4 entries."""
    return MonitorAsyncFIFO(data_width=256, depth=depth, cd_write=cd_write, cd_read=cd_read)


def MonitorPayloadFIFO(depth=512, cd_write="sys", cd_read="usb"):
    """Payload FIFO: 64-bit input, 32-bit output, 512 entries."""
    return MonitorAsyncFIFO(data_width=64, depth=depth, cd_write=cd_write, cd_read=cd_read)
