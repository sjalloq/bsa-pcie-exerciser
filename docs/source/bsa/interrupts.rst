Interrupt Requirements
======================

The BSA Exerciser must support both MSI-X and legacy INTx interrupts
to enable comprehensive interrupt subsystem testing.

MSI-X
-----

The exerciser implements full MSI-X capability:

Vector Count
~~~~~~~~~~~~

* 16 MSI-X vectors implemented
* Each vector independently configurable
* Per-vector masking support

Table Structure
~~~~~~~~~~~~~~~

Each MSI-X table entry (16 bytes) contains:

* **Message Address** (64-bit): Target address for interrupt write
* **Message Data** (32-bit): Data value written to trigger interrupt
* **Vector Control** (32-bit): Bit 0 is the mask bit

The table is accessible via BAR2. The BAR window is 32KB, but only the
first 16 entries (256 bytes) are implemented; the remaining space is reserved.

Pending Bit Array (PBA)
~~~~~~~~~~~~~~~~~~~~~~~

* One bit per vector indicating pending status (16 bits used)
* Read-only from host perspective
* Accessible via BAR5 (window is 4KB; only lower bits are valid)

Software Trigger
~~~~~~~~~~~~~~~~

Test software must be able to trigger any MSI-X vector by:

1. Writing the vector number to a control register
2. Asserting a trigger signal

The controller then:

1. Reads the table entry for that vector
2. If masked: sets the pending bit
3. If unmasked: issues Memory Write TLP to message address

Legacy INTx
-----------

The exerciser supports legacy interrupt signaling:

Level-Triggered Behavior
~~~~~~~~~~~~~~~~~~~~~~~~

* INTx is level-triggered, not edge-triggered
* Assert: Signal remains active until explicitly cleared
* Deassert: Software clears the interrupt condition

Control Interface
~~~~~~~~~~~~~~~~~

* Single control bit to assert/deassert interrupt
* Maps to PCIe ``Assert_INTx`` / ``Deassert_INTx`` messages

Use Cases
~~~~~~~~~

Legacy INTx testing validates:

* Interrupt routing through chipset
* Level-triggered interrupt handling in OS
* Interrupt sharing scenarios
