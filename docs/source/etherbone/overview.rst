Overview
========

Etherbone is CERN's protocol for running Wishbone bus operations over a network
transport. Originally designed for UDP/IP, this implementation adapts it for
USB transport via the FT601 USB 3.0 FIFO interface.

What is Etherbone?
------------------

Etherbone provides a standardized way to perform Wishbone bus transactions
remotely. It supports:

* Single and burst read operations
* Single and burst write operations
* Probe/discovery mechanism
* 32-bit or 64-bit addressing and data widths

The protocol is documented in the `Etherbone specification
<https://gitlab.com/ohwr/project/etherbone-core/-/wikis/Documents/Etherbone-full-specifications>`_.

Implementation Limitations
--------------------------

This implementation has some simplifications compared to full Etherbone:

* **32-bit only** - Fixed address and data width
* **Single record per packet** - No multi-record packets
* **No address spaces** - The ``rca``, ``bca``, ``wca``, ``wff`` flags are ignored
* **USB transport only** - No UDP/IP support

These limitations are appropriate for the CSR access use case where we perform
simple register reads and writes with occasional small bursts.

Typical Use Cases
-----------------

**Single CSR Read**::

    # Read the ID register at offset 0x48
    value = etherbone.read(0x48)
    # Returns 0xED0113B5

**Single CSR Write**::

    # Write to DMA control register
    etherbone.write(0x008, 0x00000001)

**Burst Read** (multiple addresses)::

    # Read 8 consecutive registers
    addresses = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C]
    values = etherbone.burst_read(addresses)

**Burst Write** (consecutive addresses)::

    # Write 4 values starting at address 0x100
    etherbone.burst_write(0x100, [0x11111111, 0x22222222, 0x33333333, 0x44444444])
