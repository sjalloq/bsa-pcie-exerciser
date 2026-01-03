USB Framing Layer
=================

The USB framing layer provides packet boundaries and channel multiplexing over
the raw FT601 FIFO interface. This layer is implemented in ``USBCore``
(``src/bsa_pcie_exerciser/gateware/usb/core.py``).

Frame Format
------------

Every USB frame has the following structure:

.. code-block:: text

    ┌──────────────┬──────────────┬──────────────┬─────────────────────────────┐
    │   Preamble   │   Channel    │    Length    │          Payload            │
    │   4 bytes    │   4 bytes    │   4 bytes    │       Length bytes          │
    └──────────────┴──────────────┴──────────────┴─────────────────────────────┘
          │              │              │                    │
          │              │              │                    └─► Variable length
          │              │              └─► Payload size in bytes
          │              └─► Destination channel (0-255, upper bytes ignored)
          └─► Magic value 0x5AA55AA5

Field Descriptions
------------------

Preamble (4 bytes)
^^^^^^^^^^^^^^^^^^

Fixed magic value ``0x5AA55AA5`` used for frame synchronization. The
depacketizer scans incoming data looking for this pattern to identify frame
boundaries.

The alternating bit pattern provides good synchronization properties and is
unlikely to appear randomly in payload data.

Channel (4 bytes)
^^^^^^^^^^^^^^^^^

Identifies the logical channel for this frame. Only the lower 8 bits are used,
allowing up to 256 channels:

========  ===========
Channel   Purpose
========  ===========
0         Etherbone CSR access
1         TLP Monitor stream
2-255     Reserved
========  ===========

The upper 24 bits are ignored but should be set to zero for forward
compatibility.

Length (4 bytes)
^^^^^^^^^^^^^^^^

Payload length in bytes. The depacketizer uses this to determine when the frame
ends and to generate the ``last`` signal on the final word.

.. note::

   Length is in **bytes**, not words. The hardware converts to word count
   internally using ``length[31:2]`` (integer divide by 4).

Payload
^^^^^^^

Variable-length payload data. Must be padded to a 32-bit boundary if the length
is not a multiple of 4. Padding bytes are not included in the length field.

Wire Format
-----------

Data is transmitted as 32-bit words in **little-endian** order on the FT601
interface. The frame header appears on the wire as:

.. code-block:: text

    Word 0: 0x5AA55AA5  (preamble)
    Word 1: 0x000000XX  (channel, XX = channel number)
    Word 2: 0xNNNNNNNN  (length in bytes)
    Word 3: First payload word
    Word 4: Second payload word
    ...
    Word N: Last payload word (may include padding)

Example: Etherbone Read Request
-------------------------------

A single CSR read to address ``0x48`` produces this USB frame:

.. code-block:: text

    USB Frame (32 bytes = 8 words):
    ┌────────────┬────────────┬────────────┬────────────────────────────────────┐
    │ 0x5AA55AA5 │ 0x00000000 │ 0x00000014 │      Etherbone Payload (20 bytes)  │
    │  preamble  │  channel 0 │  length 20 │                                    │
    └────────────┴────────────┴────────────┴────────────────────────────────────┘

    Breakdown by word:
    Word 0: 0x5AA55AA5  - Preamble
    Word 1: 0x00000000  - Channel 0 (Etherbone)
    Word 2: 0x00000014  - Length 20 bytes
    Word 3: 0x44106F4E  - Etherbone packet header word 0
    Word 4: 0x00000000  - Etherbone packet header word 1
    Word 5: 0x01000F10  - Record header (rcount=1)
    Word 6: 0x00000000  - Base return address
    Word 7: 0x48000000  - Read address 0x48 (big-endian in payload)

Depacketizer State Machine
--------------------------

The ``USBDepacketizer`` extracts frames from the raw stream:

.. code-block:: text

                         ┌──────────────┐
                         │              │
             ┌───────────│     IDLE     │◄─────────────────┐
             │           │              │                  │
             │           └──────┬───────┘                  │
             │                  │                          │
             │    preamble      │ preamble                 │ done or
             │    mismatch      │ match                    │ timeout
             │                  ▼                          │
             │           ┌──────────────┐                  │
             │           │   RECEIVE    │                  │
             └──────────►│   HEADER     │                  │
                         │              │                  │
                         └──────┬───────┘                  │
                                │                          │
                                │ header complete          │
                                │ (2 words: channel+len)   │
                                ▼                          │
                         ┌──────────────┐                  │
                         │              │                  │
                         │     COPY     │──────────────────┘
                         │              │
                         └──────────────┘
                           outputs payload
                           with last=1 on
                           final word

The ``last`` signal is generated combinationally:

.. code-block:: python

    # cnt increments for each word transferred
    # length[2:] converts bytes to words
    last = (cnt == source.length[2:] - 1)

This ``last`` signal is critical for downstream packet processing, particularly
the Etherbone ``PacketFIFO`` which uses it to commit packet parameters.

Timeout Handling
----------------

The depacketizer includes a configurable timeout (default 10 seconds) to recover
from corrupted or incomplete frames. If the state machine doesn't return to
IDLE within the timeout period, it forces a reset.

This prevents permanent lockup if a frame is partially received (e.g., due to
USB disconnection).
