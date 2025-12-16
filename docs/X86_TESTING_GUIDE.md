# x86 Development and Testing Guide

## Overview

You can develop and test ~80% of the BSA exerciser functionality on a standard
x86 Linux PC before moving to the ARM HAPS system. This enables faster iteration
and easier debugging.

```
DEVELOPMENT SETUP                          FINAL TARGET
================                          ============

┌─────────────────────┐                   ┌─────────────────────┐
│   x86 Linux PC      │                   │  Synopsys HAPS      │
│                     │                   │  (ARM System)       │
│  - Fast iteration   │                   │                     │
│  - Easy debugging   │     ────────►     │  - BSA ACS tests    │
│  - GDB/printf       │                   │  - SMMU validation  │
│  - No HAPS needed   │                   │  - Final compliance │
└─────────┬───────────┘                   └─────────┬───────────┘
          │                                         │
     PCIe x1                                   PCIe x1
          │                                         │
┌─────────▼───────────┐                   ┌─────────▼───────────┐
│     SPEC-A7         │                   │     SPEC-A7         │
│   (same board!)     │                   │   (same bitstream!) │
└─────────────────────┘                   └─────────────────────┘
```

## What You Can Test on x86

### Phase 1 Features (100% testable)

| Feature | How to Test | Validation |
|---------|-------------|------------|
| BAR0 registers | devmem2 or mmap | Read/write all registers |
| DMA write | Allocate buffer, trigger DMA | Verify pattern in buffer |
| DMA read | Fill buffer, trigger DMA | Read back from local mem |
| MSI-X | Trigger MSI, check interrupts | /proc/interrupts |
| No-snoop | DMA with attribute set | Wireshark/protocol analyzer |
| TXN monitor | Do config reads, check log | Read trace buffer |

### Phase 2 Features (Partially testable)

| Feature | x86 Testing | Limitation |
|---------|-------------|------------|
| PASID TLP generation | ✅ Can verify TLP has PASID prefix | VT-d PASID config differs from SMMU |
| PASID isolation | ⚠️ Requires VT-d SVA setup | Different API than ARM |
| Privilege bit | ✅ TLP attribute testable | Semantic meaning differs |

### Phase 3 Features (Partially testable)

| Feature | x86 Testing | Limitation |
|---------|-------------|------------|
| ATS requests | ✅ Intel VT-d supports ATS | Config differs from SMMU |
| ATC management | ✅ Same PCIe protocol | |
| ATS invalidation | ✅ Same PCIe protocol | Driver interface differs |

## x86 Test System Requirements

### Minimum Hardware
- x86_64 PC with available PCIe slot (x1 or larger)
- SPEC-A7 board (same one you'll use with HAPS)
- JTAG programmer (for FPGA loading)

### Recommended Hardware
- PCIe protocol analyzer (Teledyne LeCroy, Keysight) - very helpful for debugging
- Or: Use LiteScope for on-chip debugging

### Software
- Linux (Ubuntu 22.04+ recommended)
- Kernel with IOMMU support (for PASID/ATS testing)
- Vivado for bitstream generation
- Python 3.8+ for test scripts

## Kernel Configuration for Advanced Testing

For PASID/ATS testing on x86, you'll need these kernel options:

```
# IOMMU support
CONFIG_IOMMU_SUPPORT=y
CONFIG_INTEL_IOMMU=y
CONFIG_INTEL_IOMMU_SVM=y        # Shared Virtual Memory (PASID)

# For SVA (Shared Virtual Addressing)
CONFIG_IOMMU_SVA=y

# ATS support
CONFIG_PCI_ATS=y
CONFIG_PCI_PRI=y                # Page Request Interface
CONFIG_PCI_PASID=y
```

Boot parameters for testing:
```
# Enable IOMMU in passthrough mode (for basic DMA testing)
intel_iommu=on iommu=pt

# Or strict mode (for isolation testing)  
intel_iommu=on,sm_on iommu=strict
```

## Test Scenarios

### Test 1: Basic Enumeration

```bash
# After loading bitstream, verify device appears
lspci -vvv | grep -A 30 "Xilinx"

# Expected output shows:
# - Vendor/Device ID
# - BAR0 address and size
# - MSI-X capability
# - Link status (Gen2 x1)
```

### Test 2: Register Access

```bash
# Find BAR0 address from lspci output
BAR0=0xf0000000  # Example - get real address from lspci

# Read version register
sudo devmem2 $((BAR0 + 0x50)) w
# Expected: 0x00010000 (v1.0.0)

# Read capabilities
sudo devmem2 $((BAR0 + 0x4C)) w
# Expected: Shows enabled features
```

### Test 3: DMA Write (Exerciser → Host)

```python
#!/usr/bin/env python3
"""Test DMA write from exerciser to host memory."""

import mmap
import os
import ctypes

# Allocate DMA-capable buffer using hugepages
# (In production, use proper DMA allocation via driver)

def allocate_dma_buffer(size):
    """Allocate a physically contiguous buffer."""
    # Simple approach: use /dev/hugepages
    # Better: write a kernel driver with dma_alloc_coherent
    path = "/dev/hugepages/dma_test"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.ftruncate(fd, size)
    mm = mmap.mmap(fd, size)
    
    # Get physical address (requires root, reads /proc/self/pagemap)
    # This is simplified - real implementation needs pagemap parsing
    return mm, get_phys_addr(mm)

def test_dma_write(exerciser, host_buffer, phys_addr, size=4096):
    """
    Test DMA write: exerciser writes pattern to host memory.
    """
    # Clear host buffer
    host_buffer[:size] = b'\x00' * size
    
    # Configure DMA
    exerciser.write32(0x10, phys_addr & 0xFFFFFFFF)      # Bus addr lo
    exerciser.write32(0x14, (phys_addr >> 32) & 0xFFFFFFFF)  # Bus addr hi
    exerciser.write32(0x18, size)                         # Length
    exerciser.write32(0x0C, 0)                            # Local offset
    
    # Trigger DMA write (exerciser → host)
    exerciser.write32(0x08, 0x03)  # START + WRITE direction
    
    # Wait for completion
    while True:
        status = exerciser.read32(0x1C) & 0xFF
        if status == 0x02:  # Done
            break
        if status & 0x80:   # Error
            print(f"DMA error: 0x{status:02x}")
            return False
    
    # Verify data arrived
    # (Exerciser local memory should be pre-filled with test pattern)
    data = host_buffer[:size]
    print(f"Received {len(data)} bytes")
    print(f"First 16 bytes: {data[:16].hex()}")
    
    return True
```

### Test 4: MSI-X Interrupts

```bash
# Check current interrupt count
grep exerciser /proc/interrupts

# Trigger MSI vector 0
sudo devmem2 $((BAR0 + 0x00)) w 0

# Check interrupt count increased
grep exerciser /proc/interrupts
```

### Test 5: No-Snoop Attribute

Testing no-snoop requires observing the actual TLP attributes. Options:

1. **PCIe Protocol Analyzer** - See NS bit in TLP header
2. **LiteScope** - Capture TLP on FPGA side
3. **CPU cache effects** - More complex, measure timing

```python
def test_no_snoop(exerciser, phys_addr):
    """Compare DMA with and without no-snoop."""
    
    # DMA with snoop (default)
    exerciser.write32(0x08, 0x03)  # START + WRITE, no NS
    # ... wait ...
    
    # DMA without snoop
    exerciser.write32(0x08, 0x07)  # START + WRITE + NO_SNOOP
    # ... wait ...
    
    # If you have a protocol analyzer, verify TLP[4] (NS bit) differs
```

### Test 6: Transaction Monitor

```python
def test_txn_monitor(exerciser):
    """Test transaction monitoring captures config accesses."""
    
    # Clear and enable monitor
    exerciser.write32(0x44, 0x02)  # Clear
    exerciser.write32(0x44, 0x01)  # Enable
    
    # Do some config space reads (these should be captured)
    # Reading BAR0 registers generates memory transactions, not config
    # To capture config, we need the host to do config reads
    os.system("lspci -vvv -s <device> > /dev/null")
    
    # Check what was captured
    count = exerciser.read32(0x48) & 0x3F
    print(f"Captured {count} transactions")
    
    for i in range(count):
        entry = exerciser.read32(0x40)  # Auto-increments
        print(f"  TXN {i}: 0x{entry:08x}")
    
    exerciser.write32(0x44, 0x00)  # Disable
```

## PASID Testing on x86 (Phase 2)

Intel VT-d supports PASID, so you can test PASID TLP generation on x86.
However, the setup is different from ARM SMMU.

### x86 PASID Setup

```c
// Kernel driver snippet for x86 PASID allocation
#include <linux/iommu.h>

struct iommu_domain *domain;
int pasid;

// Allocate PASID
pasid = iommu_sva_bind_device(dev, current->mm, NULL);
if (pasid < 0)
    return pasid;

// Now DMA with this PASID should work
// The IOMMU will translate using current process's page tables
```

### Testing PASID TLP Generation

Even without full SVA setup, you can verify the exerciser generates
correct PASID TLP prefixes:

```python
def test_pasid_tlp_generation(exerciser):
    """Verify PASID appears in TLP (use protocol analyzer or LiteScope)."""
    
    # Set PASID value
    exerciser.write32(0x20, 0x00042)  # PASID = 0x42
    
    # Configure DMA with PASID enabled
    exerciser.write32(0x10, phys_addr & 0xFFFFFFFF)
    exerciser.write32(0x14, (phys_addr >> 32))
    exerciser.write32(0x18, 4096)
    exerciser.write32(0x0C, 0)
    
    # Trigger with PASID enabled
    exerciser.write32(0x08, 0x0B)  # START + WRITE + PASID_EN
    
    # On protocol analyzer, verify:
    # - TLP has PASID prefix (4 bytes before header)
    # - PASID value = 0x42
    # - Fmt indicates prefix present
```

## ATS Testing on x86 (Phase 3)

Intel VT-d supports ATS, so you can test ATS functionality on x86.

### Enable ATS in Config Space

First, your exerciser needs to advertise ATS capability and the host
needs to enable it:

```bash
# Check if ATS capability present
lspci -vvv -s <device> | grep -A 5 "Address Translation"

# If present, host driver should enable it
# Check ATS is enabled
setpci -s <device> ATS_CAP+6.w  # Read ATS control
```

### ATS Test Flow

```
1. Host enables ATS in exerciser config space
2. Exerciser sends Translation Request TLP
3. Root Complex (via IOMMU) responds with Translation Completion
4. Exerciser caches result in ATC
5. Subsequent DMAs use translated address
```

## Comparison: x86 vs ARM Testing

| Aspect | x86 Development | ARM (HAPS) Final |
|--------|-----------------|------------------|
| Iteration speed | Fast (minutes) | Slow (HAPS boot) |
| Debug access | Full (GDB, printf) | Limited |
| Protocol analyzer | Easy to connect | May need adapters |
| IOMMU | Intel VT-d | ARM SMMU |
| PASID API | iommu_sva_* | Same kernel API |
| ATS | Supported | Supported |
| BSA ACS | ❌ Can't run | ✅ Required |
| Final validation | ❌ Not sufficient | ✅ Required |

## Recommended Development Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DEVELOPMENT PHASES                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. SIMULATION (Verilator/cocotb)                                  │
│     └─► Verify Migen logic, register interface                     │
│                                                                     │
│  2. x86 HARDWARE (SPEC-A7 in PC)                                   │
│     └─► Verify PCIe enumeration                                    │
│     └─► Test DMA read/write                                        │
│     └─► Test MSI-X                                                 │
│     └─► Test no-snoop                                              │
│     └─► Test transaction monitor                                    │
│     └─► Debug with protocol analyzer                               │
│     └─► (Optional) Test PASID TLP generation                       │
│     └─► (Optional) Test ATS with VT-d                              │
│                                                                     │
│  3. ARM HAPS (same SPEC-A7 board)                                  │
│     └─► Verify enumeration on ARM                                  │
│     └─► Quick smoke test of basic features                         │
│     └─► Run BSA ACS test suite                                     │
│     └─► Debug any ARM-specific issues                              │
│     └─► Final compliance validation                                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## x86-Specific Test Driver

For more advanced testing, you'll want a proper Linux driver instead of
devmem2. Here's a skeleton:

```c
// bsa_exerciser_test.c - Simple test driver for x86 development

#include <linux/module.h>
#include <linux/pci.h>
#include <linux/interrupt.h>
#include <linux/dma-mapping.h>

#define EXERCISER_VENDOR_ID  0x10EE  // Xilinx (or your ID)
#define EXERCISER_DEVICE_ID  0x7021  // Your device ID

struct exerciser_dev {
    struct pci_dev *pdev;
    void __iomem *bar0;
    
    // DMA buffer
    void *dma_buf;
    dma_addr_t dma_addr;
    size_t dma_size;
    
    // MSI-X
    int num_vectors;
    struct msix_entry *msix_entries;
};

static irqreturn_t exerciser_irq(int irq, void *data)
{
    struct exerciser_dev *dev = data;
    pr_info("exerciser: MSI-X interrupt received\n");
    return IRQ_HANDLED;
}

static int exerciser_probe(struct pci_dev *pdev,
                           const struct pci_device_id *id)
{
    struct exerciser_dev *dev;
    int ret;
    
    dev = kzalloc(sizeof(*dev), GFP_KERNEL);
    if (!dev)
        return -ENOMEM;
    
    dev->pdev = pdev;
    pci_set_drvdata(pdev, dev);
    
    // Enable device
    ret = pci_enable_device(pdev);
    if (ret)
        goto err_free;
    
    // Request regions
    ret = pci_request_regions(pdev, "bsa_exerciser");
    if (ret)
        goto err_disable;
    
    // Map BAR0
    dev->bar0 = pci_iomap(pdev, 0, 0);
    if (!dev->bar0) {
        ret = -ENOMEM;
        goto err_regions;
    }
    
    // Set DMA mask
    ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
    if (ret)
        goto err_unmap;
    
    // Allocate DMA buffer
    dev->dma_size = 64 * 1024;  // 64KB
    dev->dma_buf = dma_alloc_coherent(&pdev->dev, dev->dma_size,
                                       &dev->dma_addr, GFP_KERNEL);
    if (!dev->dma_buf) {
        ret = -ENOMEM;
        goto err_unmap;
    }
    
    pr_info("exerciser: DMA buffer at virt=%px phys=%pad\n",
            dev->dma_buf, &dev->dma_addr);
    
    // Enable bus mastering (for DMA)
    pci_set_master(pdev);
    
    // Setup MSI-X
    dev->num_vectors = pci_msix_vec_count(pdev);
    if (dev->num_vectors > 0) {
        ret = pci_alloc_irq_vectors(pdev, 1, dev->num_vectors,
                                     PCI_IRQ_MSIX);
        if (ret > 0) {
            dev->num_vectors = ret;
            ret = request_irq(pci_irq_vector(pdev, 0),
                             exerciser_irq, 0, "exerciser", dev);
        }
    }
    
    // Read version
    u32 version = ioread32(dev->bar0 + 0x50);
    pr_info("exerciser: version 0x%08x\n", version);
    
    // Create sysfs entries for testing...
    
    return 0;

err_unmap:
    pci_iounmap(pdev, dev->bar0);
err_regions:
    pci_release_regions(pdev);
err_disable:
    pci_disable_device(pdev);
err_free:
    kfree(dev);
    return ret;
}

static void exerciser_remove(struct pci_dev *pdev)
{
    struct exerciser_dev *dev = pci_get_drvdata(pdev);
    
    free_irq(pci_irq_vector(pdev, 0), dev);
    pci_free_irq_vectors(pdev);
    
    if (dev->dma_buf)
        dma_free_coherent(&pdev->dev, dev->dma_size,
                          dev->dma_buf, dev->dma_addr);
    
    pci_iounmap(pdev, dev->bar0);
    pci_release_regions(pdev);
    pci_disable_device(pdev);
    kfree(dev);
}

static const struct pci_device_id exerciser_ids[] = {
    { PCI_DEVICE(EXERCISER_VENDOR_ID, EXERCISER_DEVICE_ID) },
    { 0 }
};
MODULE_DEVICE_TABLE(pci, exerciser_ids);

static struct pci_driver exerciser_driver = {
    .name     = "bsa_exerciser_test",
    .id_table = exerciser_ids,
    .probe    = exerciser_probe,
    .remove   = exerciser_remove,
};

module_pci_driver(exerciser_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("BSA Exerciser Test Driver");
```

## Summary

**Bottom line:** Yes, absolutely develop on x86 first! You can validate:
- All Phase 1 features (DMA, MSI, no-snoop, transaction monitor)
- TLP-level correctness of Phase 2/3 features (PASID prefix, ATS)

Only move to HAPS when you need to:
- Run actual BSA ACS test suite
- Validate SMMU-specific behavior
- Final compliance testing

This approach will save you significant time - x86 iteration is minutes
vs. HAPS boot/setup which can be much longer.
