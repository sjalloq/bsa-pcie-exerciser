DMA Engine
==========

The DMA subsystem consists of three components:

* **BSADMAEngine**: FSM that generates Memory Read/Write TLPs
* **BSADMABuffer**: Dual-port RAM for data storage
* **BSADMABufferHandler**: BAR1 handler for host access to buffer

Source: ``src/bsa_pcie_exerciser/dma/``

DMA Engine
----------

Source: ``src/bsa_pcie_exerciser/dma/engine.py``

The ``BSADMAEngine`` implements controlled DMA transfers with configurable
TLP attributes.

FSM States
~~~~~~~~~~

::

    IDLE ──► SETUP ──► ISSUE_RD ──► WAIT_CPL ──► COMPLETE
                  │                     │            ▲
                  │                     └────────────┤ (more data)
                  │                                  │
                  └──► LOAD_DATA ──► ISSUE_WR ───────┘

* **IDLE**: Wait for trigger from software
* **SETUP**: Calculate TLP size, initialize counters
* **ISSUE_RD**: Send Memory Read TLP (read path)
* **WAIT_CPL**: Receive completion data, store to buffer
* **LOAD_DATA**: Read data from buffer (write path)
* **ISSUE_WR**: Send Memory Write TLP
* **COMPLETE**: Signal status to software

Control Interface
~~~~~~~~~~~~~~~~~

The engine connects to BSA registers:

.. code-block:: python

    self.comb += [
        self.dma_engine.trigger.eq(self.bsa_regs.dma_trigger),
        self.dma_engine.direction.eq(self.bsa_regs.dma_direction),
        self.dma_engine.no_snoop.eq(self.bsa_regs.dma_no_snoop),
        self.dma_engine.addr_type.eq(self.bsa_regs.dma_addr_type),
        self.dma_engine.bus_addr.eq(self.bsa_regs.dma_bus_addr),
        self.dma_engine.length.eq(self.bsa_regs.dma_len),
        self.dma_engine.offset.eq(self.bsa_regs.dma_offset),
        # PASID signals...
    ]

TLP Attribute Generation
~~~~~~~~~~~~~~~~~~~~~~~~

The engine sets TLP attributes based on control signals:

.. code-block:: python

    self.comb += [
        source.attr.eq(Cat(current_ns, 0)),  # [0]=No-Snoop
        source.at.eq(current_at),            # Address Type
        # PASID fields travel through stream
        source.pasid_en.eq(current_pasid_en),
        source.pasid_val.eq(current_pasid_val),
        source.privileged.eq(current_priv),
        source.execute.eq(current_exec),
    ]

ATC Integration
~~~~~~~~~~~~~~~

When ``use_atc`` is enabled, the engine uses translated addresses:

.. code-block:: python

    # Effective address uses ATC translation if available
    effective_addr = Signal(64)
    self.comb += effective_addr.eq(
        Mux(addr_in_atc, self.atc_output_addr, current_addr)
    )

    # In ISSUE_RD/ISSUE_WR states
    source.adr.eq(effective_addr),

DMA Buffer
----------

Source: ``src/bsa_pcie_exerciser/dma/buffer.py``

The buffer is a dual-port RAM with two implementations:

* **_BSADMABufferMigen**: Uses Migen Memory (for simulation)
* **_BSADMABufferXPM**: Uses Xilinx XPM TDPRAM (for synthesis)

A factory function selects the implementation:

.. code-block:: python

    def BSADMABuffer(size=16*1024, data_width=64, simulation=False):
        if simulation:
            return _BSADMABufferMigen(size, data_width)
        else:
            return _BSADMABufferXPM(size, data_width)

Port A (DMA Engine)
~~~~~~~~~~~~~~~~~~~

Used by the DMA engine FSM:

* Writes when receiving completion data (DMA read from host)
* Reads when sending write data (DMA write to host)

Port B (BAR1 Handler)
~~~~~~~~~~~~~~~~~~~~~

Used by the TLP handler for host access:

* Byte-granular write enables for partial writes
* Supports host read/write via BAR1 memory space

DMA Buffer Handler
------------------

Source: ``src/bsa_pcie_exerciser/dma/handler.py``

The ``BSADMABufferHandler`` handles PCIe requests targeting BAR1:

* Implements ``req_sink`` and ``cpl_source`` endpoints
* Translates TLP addresses to buffer offsets
* Generates completion TLPs for read requests
* Uses Port B of the dual-port buffer

Data Flow Example
-----------------

DMA Read (Host → Exerciser)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Software writes target address to ``DMA_BUS_ADDR``
2. Software writes length to ``DMA_LEN``
3. Software writes buffer offset to ``DMA_OFFSET``
4. Software sets ``DIRECTION=0`` and triggers via ``DMACTL``
5. Engine issues Memory Read TLP
6. Engine receives completion data, writes to buffer via Port A
7. Engine signals completion via ``DMASTATUS``
8. Software reads data from buffer via BAR1 (Port B)

DMA Write (Exerciser → Host)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Software writes data to buffer via BAR1 (Port B)
2. Software configures address, length, offset
3. Software sets ``DIRECTION=1`` and triggers
4. Engine reads from buffer via Port A
5. Engine issues Memory Write TLP(s)
6. Engine signals completion (no completion TLP for posted writes)
