PCIeSquirrel / CaptainDMA Board
===============================

The PCIeSquirrel and CaptainDMA 35t484 are functionally identical boards based
on the Xilinx Artix-7 XC7A35T FPGA. They provide PCIe Gen2 x1 connectivity and
USB 3.0 via an FTDI FT601 chip.

.. note::

   The CaptainDMA "4.1th" refers to this board family. The pcileech-fpga
   repository confirms PCIeSquirrel and CaptainDMA share identical constraints.

Specifications
--------------

.. list-table::
   :widths: 30 70

   * - FPGA
     - Xilinx XC7A35T-FGG484-2 (Artix-7, 484-ball BGA)
   * - PCIe
     - Gen2 x1 (5 GT/s, ~500 MB/s)
   * - USB
     - USB 3.0 SuperSpeed via FTDI FT601Q
   * - System Clock
     - 100 MHz oscillator
   * - User I/O
     - 2 LEDs, 2 switches/buttons

Block Diagram
-------------

::

                         +---------------------------+
                         |      XC7A35T-FGG484       |
                         |                           |
    PCIe x1  <===========>  GTP      +----------+   |
    (Gen2)               |  Lane     | LitePCIe |   |
                         |           +----------+   |
                         |                |         |
                         |           +----------+   |
                         |           |   SoC    |   |
                         |           +----------+   |
                         |                |         |
    USB 3.0  <===========>  FT601    +----------+   |
    (Host)               |  PHY      |  USBCore |   |
                         |           +----------+   |
                         |                           |
                         +---------------------------+

Interfaces
----------

PCIe Interface
^^^^^^^^^^^^^^

The PCIe interface uses a dedicated GTP transceiver. These pins are fixed by
the FPGA architecture and are common across all FGG484 Artix-7 boards.

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Signal
     - Pin
     - Description
   * - ``pcie_rx_p``
     - B10
     - Receive differential pair (positive)
   * - ``pcie_rx_n``
     - A10
     - Receive differential pair (negative)
   * - ``pcie_tx_p``
     - B6
     - Transmit differential pair (positive)
   * - ``pcie_tx_n``
     - A6
     - Transmit differential pair (negative)
   * - ``pcie_clk_p``
     - F6
     - Reference clock 100MHz (positive)
   * - ``pcie_clk_n``
     - E6
     - Reference clock 100MHz (negative)
   * - ``pcie_perst_n``
     - B13
     - Fundamental reset (active low)
   * - ``pcie_present``
     - A13
     - Card present detect
   * - ``pcie_wake_n``
     - A14
     - Wake signal (active low)

USB 3.0 Interface (FT601)
^^^^^^^^^^^^^^^^^^^^^^^^^

The FTDI FT601Q provides USB 3.0 SuperSpeed connectivity using a 32-bit
synchronous FIFO interface. See :doc:`ft601` for protocol details.

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Signal
     - Pin
     - Description
   * - ``ft601_clk``
     - W19
     - 100 MHz clock output from FT601
   * - ``ft601_data[31:0]``
     - (32 pins)
     - Bidirectional data bus
   * - ``ft601_be[3:0]``
     - Y18, AA18, AB18, W17
     - Byte enable (active high)
   * - ``ft601_rxf_n``
     - AB8
     - RX FIFO not empty (active low = data available)
   * - ``ft601_txe_n``
     - AA8
     - TX FIFO not full (active low = can write)
   * - ``ft601_rd_n``
     - AA6
     - Read strobe (active low)
   * - ``ft601_wr_n``
     - AB7
     - Write strobe (active low)
   * - ``ft601_oe_n``
     - AB6
     - Output enable for data bus (active low)
   * - ``ft601_siwu_n``
     - Y8
     - Send immediate / wake up (active low)
   * - ``ft601_rst_n``
     - Y9
     - Reset (active low)

**Data Bus Pins:**
N13, N14, N15, P15, P16, N17, P17, R17, P19, R18, R19, T18, U18, V18, V19, V17,
W20, Y19, T21, T20, U21, V20, W22, W21, Y22, Y21, AA21, AB22, AA20, AB21, AA19, AB20

System Clock
^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Signal
     - Pin
     - Description
   * - ``clk``
     - H4
     - 100 MHz system oscillator (LVCMOS33)

User I/O
^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Signal
     - Pin
     - Description
   * - ``user_led1``
     - Y6
     - User LED 1 (active high)
   * - ``user_led2``
     - AB5
     - User LED 2 (active high)
   * - ``user_sw1_n``
     - AB3
     - User switch 1 (active low)
   * - ``user_sw2_n``
     - AA5
     - User switch 2 (active low)
   * - ``ft2232_rst_n``
     - F21
     - FT2232 reset (active low, optional JTAG interface)

Timing Constraints
------------------

The pcileech-fpga project provides validated timing constraints for the FT601
interface:

.. code-block:: tcl

   # FT601 clock
   create_clock -period 10.000 -name ft601_clk [get_ports ft601_clk]

   # Input delays (FT601 → FPGA)
   set_input_delay -clock ft601_clk -min 6.5 [get_ports {ft601_data[*]}]
   set_input_delay -clock ft601_clk -max 7.0 [get_ports {ft601_data[*]}]
   set_input_delay -clock ft601_clk -min 6.5 [get_ports {ft601_rxf_n ft601_txe_n}]
   set_input_delay -clock ft601_clk -max 7.0 [get_ports {ft601_rxf_n ft601_txe_n}]

   # Output delays (FPGA → FT601)
   set_output_delay -clock ft601_clk -min 4.8 [get_ports {ft601_wr_n ft601_rd_n ft601_oe_n}]
   set_output_delay -clock ft601_clk -max 1.0 [get_ports {ft601_wr_n ft601_rd_n ft601_oe_n}]
   set_output_delay -clock ft601_clk -min 4.8 [get_ports {ft601_be[*] ft601_data[*]}]
   set_output_delay -clock ft601_clk -max 1.0 [get_ports {ft601_be[*] ft601_data[*]}]

References
----------

* pcileech-fpga constraints: ``external/pcileech-fpga/PCIeSquirrel/src/pcileech_squirrel.xdc``
* FTDI FT601 Datasheet: `FTDI Website <https://ftdichip.com/products/ft601q-b/>`_
