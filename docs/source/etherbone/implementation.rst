Implementation Details
======================

This section covers the hardware implementation of the USB Etherbone stack,
including the module hierarchy and important design considerations.

Module Hierarchy
----------------

The USB Etherbone subsystem consists of these modules:

.. code-block:: text

    SquirrelSoC
    │
    ├── USBCore                    (src/bsa_pcie_exerciser/gateware/usb/core.py)
    │   ├── USBPacketizer          Adds USB framing to outbound data
    │   ├── USBDepacketizer        Strips USB framing from inbound data
    │   └── USBCrossbar            Routes packets by channel ID
    │
    └── Etherbone                  (src/bsa_pcie_exerciser/gateware/usb/etherbone.py)
        ├── EtherbonePacketRX      Parses packet headers, detects probes
        ├── EtherbonePacketTX      Generates packet headers for responses
        ├── EtherboneRecordReceiver  Parses records, executes Wishbone ops
        ├── EtherboneRecordSender    Builds response records
        └── EtherboneMaster        Wishbone master interface

Data Flow
---------

**Inbound (Host to Device):**

.. code-block:: text

    FT601 PHY
        │
        ▼
    USBDepacketizer ──────► extracts channel, length, sets 'last' on final word
        │
        ▼
    USBCrossbar ──────────► routes to channel 0 (Etherbone)
        │
        ▼
    EtherbonePacketRX ────► validates magic, handles probes
        │
        ▼
    EtherboneRecordReceiver ► parses record header, executes reads/writes
        │
        ▼
    EtherboneMaster ──────► Wishbone bus transactions

**Outbound (Device to Host):**

.. code-block:: text

    EtherboneMaster ──────► provides read data
        │
        ▼
    EtherboneRecordSender ► builds response record
        │
        ▼
    EtherbonePacketTX ────► adds packet header
        │
        ▼
    USBCrossbar ──────────► routes to packetizer
        │
        ▼
    USBPacketizer ────────► adds USB framing (preamble, channel, length)
        │
        ▼
    FT601 PHY

PacketFIFO and the 'last' Signal
--------------------------------

The ``EtherboneRecordReceiver`` uses a ``PacketFIFO`` to buffer incoming data.
This FIFO has a critical property: **parameters are only committed when the
``last`` signal is asserted**.

.. code-block:: text

    PacketFIFO Structure:
    ┌─────────────────────────────────────────────────────────────────────────┐
    │                                                                         │
    │   ┌──────────────┐          ┌──────────────┐          ┌──────────────┐  │
    │   │   Payload    │          │   Params     │          │    Output    │  │
    │   │    FIFO      │─────────►│   Commit     │─────────►│              │  │
    │   │              │          │   Logic      │          │              │  │
    │   └──────────────┘          └──────────────┘          └──────────────┘  │
    │         ▲                         ▲                                     │
    │         │                         │                                     │
    │      payload                   'last'                                   │
    │      words                    signal                                    │
    │                                                                         │
    └─────────────────────────────────────────────────────────────────────────┘

The FSM that drains the PacketFIFO waits for valid parameters before processing:

.. code-block:: python

    # From EtherboneRecordReceiver
    fsm.act("IDLE",
        fifo.source.ready.eq(1),
        If(fifo.source.valid,  # Valid only after 'last' commits params
            If(fifo.source.wcount,
                NextState("RECEIVE_WRITES")
            ).Elif(fifo.source.rcount,
                NextState("RECEIVE_READS")
            )
        )
    )

This creates a dependency: the FSM won't drain the payload FIFO until params
are valid, but params aren't valid until ``last`` arrives.

Buffer Depth Deadlock
---------------------

With insufficient buffer depth, burst operations can deadlock:

**The Problem:**

Consider a burst read of 8 addresses with ``buffer_depth=4``:

.. code-block:: text

    Etherbone payload (9 words total):
    ┌──────────────────────────────────────────────────────────────────────┐
    │ Record │  Base  │ Addr 0 │ Addr 1 │ Addr 2 │ Addr 3 │ Addr 4 │ ...  │
    │ Header │  Addr  │        │        │        │        │        │      │
    └──────────────────────────────────────────────────────────────────────┘
       Word 0  Word 1   Word 2   Word 3   Word 4   Word 5   Word 6   ...

With a 4-word payload FIFO:

1. Words 0-3 fill the FIFO → FIFO full, backpressure asserted
2. Upstream (USBDepacketizer) stalls, cannot send more words
3. USBDepacketizer never reaches word count that triggers ``last``
4. Without ``last``, params never commit
5. Without valid params, FSM stays in IDLE, FIFO never drains
6. **Deadlock**: FIFO full, waiting to drain; FSM waiting for params

.. code-block:: text

    ┌──────────────────────────────────────────────────────────────────────────┐
    │                         DEADLOCK CYCLE                                   │
    │                                                                          │
    │    ┌─────────────────┐                       ┌─────────────────┐         │
    │    │                 │    backpressure       │                 │         │
    │    │  Payload FIFO   │◄──────────────────────│ USBDepacketizer │         │
    │    │    (FULL)       │                       │   (STALLED)     │         │
    │    │                 │                       │                 │         │
    │    └────────┬────────┘                       └────────┬────────┘         │
    │             │                                         │                  │
    │             │ waiting for                             │ cannot send      │
    │             │ valid params                            │ remaining words  │
    │             │                                         │                  │
    │             ▼                                         │                  │
    │    ┌─────────────────┐                               │                  │
    │    │                 │    'last' never arrives       │                  │
    │    │   Params FIFO   │◄──────────────────────────────┘                  │
    │    │   (WAITING)     │                                                   │
    │    │                 │                                                   │
    │    └─────────────────┘                                                   │
    │                                                                          │
    └──────────────────────────────────────────────────────────────────────────┘

**The Solution:**

The ``buffer_depth`` must accommodate the **entire** record payload:

.. code-block:: text

    Minimum buffer depth = 1 (record header)
                         + 1 (base address)
                         + max(wcount, rcount)

For a burst of N addresses: ``buffer_depth >= N + 2``

The ``SquirrelSoC`` sets ``buffer_depth=16`` to support bursts up to 14
addresses without deadlock:

.. code-block:: python

    # From squirrel.py
    self.etherbone = Etherbone(self.usb_core, channel_id=0, buffer_depth=16)

The ``last`` Signal Generation
------------------------------

The USBDepacketizer generates the ``last`` signal based on word count:

.. code-block:: python

    # Simplified from USBDepacketizer
    cnt = Signal(32)  # Word counter

    # Increment counter for each word transferred
    If(source.valid & source.ready,
        cnt.eq(cnt + 1)
    )

    # 'last' when we've transferred length/4 words
    # (length is in bytes, we transfer 32-bit words)
    last = (cnt == source.length[2:] - 1)

The ``length[2:]`` expression converts bytes to words by discarding the lower
2 bits (equivalent to integer divide by 4).

For a 20-byte Etherbone payload (5 words):

- ``length = 20 = 0x14``
- ``length[2:] = 5``
- ``last`` asserts when ``cnt == 4`` (0-indexed, so 5th word)

Clock Domain Considerations
---------------------------

The USB PHY operates at 100MHz (FT601 clock) while the system runs at 125MHz.
The ``USBCore`` handles clock domain crossing:

.. code-block:: text

    USB Clock Domain (100MHz)              System Clock Domain (125MHz)
    ┌────────────────────────┐             ┌────────────────────────┐
    │                        │             │                        │
    │      FT601Sync         │             │      Etherbone         │
    │      (USB PHY)         │             │                        │
    │                        │             │                        │
    └───────────┬────────────┘             └───────────┬────────────┘
                │                                      │
                │         ┌──────────────┐             │
                │         │              │             │
                └────────►│  Async FIFO  │─────────────┘
                          │  (in USBCore)│
                          │              │
                          └──────────────┘

The async FIFOs in USBCore handle the domain crossing safely. In simulation,
the FT601Stub operates directly in the system clock domain, bypassing the
CDC logic for deterministic testing.

Timeout Recovery
----------------

The depacketizer includes a timeout mechanism to recover from incomplete
frames (default 10 seconds at system clock frequency):

.. code-block:: python

    # Timeout counter
    timeout_cnt = Signal(max=int(sys_clk_freq * timeout))

    # In non-IDLE states, increment counter
    # If counter reaches max, force return to IDLE
    If(timeout_cnt == int(sys_clk_freq * timeout) - 1,
        NextState("IDLE"),
        timeout_cnt.eq(0)
    )

This prevents permanent lockup if a USB packet is partially received (e.g.,
due to cable disconnect or transmission error).

Performance Considerations
--------------------------

The USB Etherbone path has these characteristics:

**Throughput:**

- FT601 theoretical max: 400 MB/s (32-bit @ 100MHz)
- Practical limit: ~200-300 MB/s due to USB protocol overhead
- Single CSR access: ~1-2 us latency

**Burst Efficiency:**

Burst operations are more efficient than individual accesses:

.. code-block:: text

    Single read overhead:
      USB frame header:     12 bytes
      Etherbone header:      8 bytes
      Record header:         4 bytes
      Base addr + read addr: 8 bytes
      ─────────────────────────────────
      Total overhead:       32 bytes for 4 bytes of data

    Burst of 8 reads:
      Total overhead:       32 bytes for 32 bytes of data (8 addresses)
      Response:             32 bytes for 32 bytes of data

For CSR access patterns, individual reads are acceptable. For bulk data
transfer (e.g., reading large memory regions), bursts significantly improve
efficiency.
