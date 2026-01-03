USB Etherbone Protocol
======================

The BSA PCIe Exerciser uses Etherbone over USB to provide CSR access from the
host PC. This section documents the complete protocol stack from USB framing
through to Wishbone transactions.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   overview
   usb_framing
   packet_format
   implementation

Protocol Stack Overview
-----------------------

Data flows through multiple protocol layers, each adding its own framing:

.. code-block:: text

    ┌─────────────────────────────────────────────────────────┐
    │                    Application                          │
    │              (CSR Read/Write Request)                   │
    └─────────────────────────┬───────────────────────────────┘
                              │
    ┌─────────────────────────▼───────────────────────────────┐
    │              Etherbone Record Layer                     │
    │         (base address, read/write addresses)            │
    └─────────────────────────┬───────────────────────────────┘
                              │
    ┌─────────────────────────▼───────────────────────────────┐
    │              Etherbone Packet Layer                     │
    │            (magic, version, probe flags)                │
    └─────────────────────────┬───────────────────────────────┘
                              │
    ┌─────────────────────────▼───────────────────────────────┐
    │                 USB Channel Layer                       │
    │           (preamble, channel ID, length)                │
    └─────────────────────────┬───────────────────────────────┘
                              │
    ┌─────────────────────────▼───────────────────────────────┐
    │                   FT601 USB PHY                         │
    │              (32-bit FIFO interface)                    │
    └─────────────────────────────────────────────────────────┘

Channel Allocation
------------------

The USB channel multiplexer supports multiple logical channels over a single
USB connection:

========  ===========  ==========================================
Channel   Name         Purpose
========  ===========  ==========================================
0         Etherbone    CSR access via Wishbone
1         Monitor      TLP capture stream (RX and TX)
2-255     Reserved     Available for future use
========  ===========  ==========================================
