Etherbone Packet Format
=======================

Etherbone packets are carried inside USB frames on channel 0. Each Etherbone
packet contains a header followed by one or more records.

Packet Structure
----------------

A complete Etherbone packet has this structure:

.. code-block:: text

    ┌──────────────────────────────────────────────────────────────────────────┐
    │                        Etherbone Packet Header                           │
    │                             (8 bytes)                                    │
    ├──────────────────────────────────────────────────────────────────────────┤
    │                        Etherbone Record 0                                │
    │                    (4 bytes + variable payload)                          │
    ├──────────────────────────────────────────────────────────────────────────┤
    │                        Etherbone Record 1                                │
    │                           (optional)                                     │
    ├──────────────────────────────────────────────────────────────────────────┤
    │                             ...                                          │
    └──────────────────────────────────────────────────────────────────────────┘

.. note::

   This implementation supports only **single-record packets**. Multi-record
   packets are defined by the Etherbone specification but not implemented here.

Packet Header
-------------

The 8-byte packet header identifies the packet as Etherbone and specifies
protocol parameters:

.. code-block:: text

    Byte:   0      1      2      3      4      5      6      7
         ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
         │    Magic    │Flags │ Size │         Reserved          │
         │   0x4E6F    │      │      │                           │
         └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘

Magic (Bytes 0-1)
^^^^^^^^^^^^^^^^^

Fixed value ``0x4E6F`` (big-endian), which spells "No" in ASCII. This identifies
the packet as Etherbone and allows receivers to detect protocol mismatches.

Flags (Byte 2)
^^^^^^^^^^^^^^

.. code-block:: text

    Bit:  7   6   5   4   3   2   1   0
        ┌───┬───┬───┬───┬───┬───┬───┬───┐
        │  Version  │Rsv│ NR│ PR│ PF│
        │   (4 bits)│   │   │   │   │
        └───┴───┴───┴───┴───┴───┴───┴───┘

=========  ====  ===========================================================
Field      Bits  Description
=========  ====  ===========================================================
Version    7:4   Protocol version (must be 1)
Reserved   3     Reserved, set to 0
NR         2     No Reads - if set, device must not send read responses
PR         1     Probe Response - set in response to a probe
PF         0     Probe Flag - requests a probe response from device
=========  ====  ===========================================================

Size (Byte 3)
^^^^^^^^^^^^^

Encodes the address and data widths:

.. code-block:: text

    Bit:  7   6   5   4   3   2   1   0
        ┌───┬───┬───┬───┬───┬───┬───┬───┐
        │  Address Size │   Port Size   │
        │   (4 bits)    │   (4 bits)    │
        └───┴───┴───┴───┴───┴───┴───┴───┘

=========  ====  ===========================================================
Field      Bits  Description
=========  ====  ===========================================================
Addr Size  7:4   Address width: 1=8b, 2=16b, 4=32b, 8=64b
Port Size  3:0   Data width: 1=8b, 2=16b, 4=32b, 8=64b
=========  ====  ===========================================================

This implementation uses ``0x44`` (32-bit addresses, 32-bit data).

Reserved (Bytes 4-7)
^^^^^^^^^^^^^^^^^^^^

Must be zero. Padding to align the header to 8 bytes.

Record Format
-------------

Each record describes a single Wishbone transaction (read or write). Records
can contain multiple addresses for burst operations.

Record Header
^^^^^^^^^^^^^

The 4-byte record header:

.. code-block:: text

    Byte:   0      1      2      3
         ┌──────┬──────┬──────┬──────┐
         │Flags │  BE  │WCount│RCount│
         └──────┴──────┴──────┴──────┘

Flags (Byte 0)
""""""""""""""

.. code-block:: text

    Bit:  7   6   5   4   3   2   1   0
        ┌───┬───┬───┬───┬───┬───┬───┬───┐
        │Rsv│WFF│WCA│CYC│Rsv│RFF│RCA│BCA│
        └───┴───┴───┴───┴───┴───┴───┴───┘

=========  ====  ===========================================================
Field      Bit   Description
=========  ====  ===========================================================
Reserved   7     Reserved
WFF        6     Write FIFO - writes go to same address (FIFO mode)
WCA        5     Write Config - target config space (not memory)
CYC        4     Cycle - assert Wishbone CYC for duration of record
Reserved   3     Reserved
RFF        2     Read FIFO - reads from same address (FIFO mode)
RCA        1     Read Config - source is config space
BCA        0     Base Config - base address is in config space
=========  ====  ===========================================================

.. note::

   This implementation ignores all flags except CYC. The address space flags
   (WCA, RCA, BCA) and FIFO flags (WFF, RFF) are not used.

Byte Enable (Byte 1)
""""""""""""""""""""

4-bit byte enable for partial word access, stored in the lower nibble:

=========  ===========================================================
Value      Access
=========  ===========================================================
0x0F       Full 32-bit word (default)
0x01       Byte 0 only
0x03       Lower 16 bits (half-word)
0x0C       Upper 16 bits
=========  ===========================================================

WCount (Byte 2)
"""""""""""""""

Number of write data words to follow. Zero means no writes.

RCount (Byte 3)
"""""""""""""""

Number of read addresses to follow. Zero means no reads.

Read Operation
--------------

A read request contains addresses to read from. The device responds with data
read from those addresses.

Request Format
^^^^^^^^^^^^^^

.. code-block:: text

    ┌───────────────────────────────────────────────────────────────────────┐
    │              Etherbone Packet Header (8 bytes)                        │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Record Header (4 bytes)                                  │
    │              wcount=0, rcount=N                                       │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Base Return Address (4 bytes, big-endian)                │
    │              (Address where response data should be written)          │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Address 0 (4 bytes, big-endian)                     │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Address 1 (4 bytes, big-endian)                     │
    ├───────────────────────────────────────────────────────────────────────┤
    │              ...                                                      │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Address N-1 (4 bytes, big-endian)                   │
    └───────────────────────────────────────────────────────────────────────┘

The **base return address** specifies where response data should be written
in the requester's address space. This implementation sets it to zero as
responses are sent back as Etherbone packets rather than DMA writes.

Response Format
^^^^^^^^^^^^^^^

.. code-block:: text

    ┌───────────────────────────────────────────────────────────────────────┐
    │              Etherbone Packet Header (8 bytes)                        │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Record Header (4 bytes)                                  │
    │              wcount=N, rcount=0                                       │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Base Write Address (4 bytes, big-endian)                 │
    │              (Copy of requester's base return address)                │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Data 0 (4 bytes, big-endian)                        │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Data 1 (4 bytes, big-endian)                        │
    ├───────────────────────────────────────────────────────────────────────┤
    │              ...                                                      │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Read Data N-1 (4 bytes, big-endian)                      │
    └───────────────────────────────────────────────────────────────────────┘

The response is a write record (wcount=N) containing the data values read
from the requested addresses.

Write Operation
---------------

A write request contains a base address and data words to write.

Request Format
^^^^^^^^^^^^^^

.. code-block:: text

    ┌───────────────────────────────────────────────────────────────────────┐
    │              Etherbone Packet Header (8 bytes)                        │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Record Header (4 bytes)                                  │
    │              wcount=N, rcount=0                                       │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Base Write Address (4 bytes, big-endian)                 │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Write Data 0 (4 bytes, big-endian)                       │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Write Data 1 (4 bytes, big-endian)                       │
    ├───────────────────────────────────────────────────────────────────────┤
    │              ...                                                      │
    ├───────────────────────────────────────────────────────────────────────┤
    │              Write Data N-1 (4 bytes, big-endian)                     │
    └───────────────────────────────────────────────────────────────────────┘

For burst writes, addresses auto-increment from the base address:

- Write 0 goes to ``base_address + 0``
- Write 1 goes to ``base_address + 4``
- Write N-1 goes to ``base_address + 4*(N-1)``

Writes do not generate a response packet.

Byte Ordering
-------------

Etherbone uses **big-endian** byte ordering for all multi-byte fields within
the Etherbone payload:

- Packet header magic (0x4E6F)
- Addresses (both base and read/write addresses)
- Data values

This is the native byte order specified by the Etherbone protocol.

However, the USB transport layer uses **little-endian** ordering for the
32-bit words transmitted over the FT601 FIFO. This means:

1. USB frame header (preamble, channel, length) - little-endian
2. Etherbone payload - big-endian

The hardware handles this by treating the Etherbone payload as opaque bytes
that are byte-swapped when converting between the 32-bit FIFO interface and
the byte-oriented Etherbone format.

Example: CSR Read at Address 0x48
---------------------------------

Reading the BSA ID register at offset ``0x48``:

**Request packet (host to device):**

.. code-block:: text

    Etherbone Header (8 bytes):
      Byte 0-1: 0x4E 0x6F        Magic "No"
      Byte 2:   0x10             Version=1, no flags
      Byte 3:   0x44             32-bit addr, 32-bit data
      Byte 4-7: 0x00 0x00 0x00 0x00   Reserved

    Record Header (4 bytes):
      Byte 0:   0x10             CYC=1
      Byte 1:   0x0F             Full word access
      Byte 2:   0x00             wcount=0
      Byte 3:   0x01             rcount=1

    Base Return Address (4 bytes):
      0x00 0x00 0x00 0x00        Not used

    Read Address (4 bytes):
      0x00 0x00 0x00 0x48        Address 0x48 (big-endian)

    Total: 20 bytes

**Response packet (device to host):**

.. code-block:: text

    Etherbone Header (8 bytes):
      Byte 0-1: 0x4E 0x6F        Magic
      Byte 2:   0x10             Version=1
      Byte 3:   0x44             32-bit addr/data
      Byte 4-7: 0x00 0x00 0x00 0x00   Reserved

    Record Header (4 bytes):
      Byte 0:   0x10             CYC=1
      Byte 1:   0x0F             Full word
      Byte 2:   0x01             wcount=1 (response data)
      Byte 3:   0x00             rcount=0

    Base Write Address (4 bytes):
      0x00 0x00 0x00 0x00        Copy of request's base return addr

    Read Data (4 bytes):
      0xED 0x01 0x13 0xB5        Value at 0x48 (big-endian)

    Total: 20 bytes

Probe Operation
---------------

Probing discovers Etherbone endpoints and negotiates parameters.

**Probe request:**

.. code-block:: text

    Etherbone Header only (8 bytes):
      Byte 0-1: 0x4E 0x6F        Magic
      Byte 2:   0x11             Version=1, PF=1 (probe flag)
      Byte 3:   0x44             32-bit addr/data
      Byte 4-7: 0x00 0x00 0x00 0x00

No records follow a probe request.

**Probe response:**

.. code-block:: text

    Etherbone Header only (8 bytes):
      Byte 0-1: 0x4E 0x6F        Magic
      Byte 2:   0x12             Version=1, PR=1 (probe response)
      Byte 3:   0x44             Device's supported sizes
      Byte 4-7: 0x00 0x00 0x00 0x00

The probe response echoes back the device's supported address and data widths.
