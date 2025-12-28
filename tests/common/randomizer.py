#
# BSA PCIe Exerciser - Constrained-Random TLP Generator
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Constrained-random stimulus generation for PCIe TLP testing.

Provides:
- TLPRandomizer: Generates random but legal TLP parameters
- Constraints: Configurable bounds for different test scenarios
- History tracking for debug/reproduction

Usage:
    rand = TLPRandomizer(seed=12345, constraints=BAR0_REGISTER_CONSTRAINTS)
    params = rand.generate_mwr_params()
    # params contains: address, length_dw, data, tag, attr, at, first_be, last_be
"""

import random
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any


@dataclass
class TLPConstraints:
    """Constraints for TLP parameter generation."""
    
    # Address constraints
    min_addr: int = 0
    max_addr: int = 0xFFFF_FFFF
    addr_alignment: int = 4  # DWORD aligned minimum
    force_64bit_prob: float = 0.0  # Probability of 64-bit address
    
    # Length constraints (in DWORDs)
    min_length_dw: int = 1
    max_length_dw: int = 128  # 512 bytes max
    
    # Attribute probabilities
    no_snoop_prob: float = 0.3
    relaxed_order_prob: float = 0.2
    
    # AT field weights: [untranslated, trans_req, translated, reserved]
    at_weights: Tuple[float, ...] = (0.7, 0.1, 0.2, 0.0)
    
    # Tag constraints
    min_tag: int = 0
    max_tag: int = 255
    
    # Byte enable
    allow_partial_be: bool = True
    partial_be_prob: float = 0.2
    
    # Requester ID
    vary_requester_id: bool = False
    requester_id: int = 0x0100


class TLPRandomizer:
    """Constrained-random TLP parameter generator."""
    
    def __init__(self, seed: Optional[int] = None, 
                 constraints: Optional[TLPConstraints] = None):
        """
        Initialize randomizer.
        
        Args:
            seed: Random seed for reproducibility. If None, uses system entropy.
            constraints: Parameter constraints. If None, uses defaults.
        """
        self.seed = seed
        self.rng = random.Random(seed)
        self.constraints = constraints or TLPConstraints()
        self.generated_count = 0
        self.history: List[Dict[str, Any]] = []
    
    def get_state(self) -> Dict[str, Any]:
        """Get current state for reproduction."""
        return {
            'seed': self.seed,
            'generated_count': self.generated_count,
        }
    
    def random_address(self, force_64bit: bool = False) -> int:
        """Generate random aligned address."""
        c = self.constraints
        
        if force_64bit or self.rng.random() < c.force_64bit_prob:
            # 64-bit address (above 4GB)
            high = self.rng.randint(1, 0xFFFF)
            low = self.rng.randint(c.min_addr, 0xFFFF_FFFF)
            addr = (high << 32) | low
        else:
            addr = self.rng.randint(c.min_addr, c.max_addr)
        
        return addr & ~(c.addr_alignment - 1)
    
    def random_length_dw(self) -> int:
        """Generate random length in DWORDs with bias toward smaller sizes."""
        c = self.constraints
        
        # Exponential-like distribution
        r = self.rng.random()
        if r < 0.5:
            # 50%: 1-4 DW (most common)
            return self.rng.randint(c.min_length_dw, min(4, c.max_length_dw))
        elif r < 0.9:
            # 40%: 5-32 DW
            return self.rng.randint(min(5, c.max_length_dw), 
                                   min(32, c.max_length_dw))
        else:
            # 10%: full range
            return self.rng.randint(c.min_length_dw, c.max_length_dw)
    
    def random_attr(self) -> int:
        """Generate random TLP attributes."""
        c = self.constraints
        attr = 0
        if self.rng.random() < c.no_snoop_prob:
            attr |= 0x1  # No-Snoop
        if self.rng.random() < c.relaxed_order_prob:
            attr |= 0x2  # Relaxed Ordering
        return attr
    
    def random_at(self) -> int:
        """Generate random Address Type field."""
        c = self.constraints
        r = self.rng.random()
        cumulative = 0.0
        for i, weight in enumerate(c.at_weights):
            cumulative += weight
            if r < cumulative:
                return i
        return 0
    
    def random_tag(self) -> int:
        """Generate random tag."""
        c = self.constraints
        return self.rng.randint(c.min_tag, c.max_tag)
    
    def random_byte_enables(self, length_dw: int) -> Tuple[int, int]:
        """
        Generate random first_be and last_be.
        
        PCIe byte enable rules:
        - first_be: Which bytes of first DW are valid
        - last_be: Which bytes of last DW are valid (0 if length=1)
        """
        c = self.constraints
        
        if not c.allow_partial_be or self.rng.random() > c.partial_be_prob:
            first_be = 0xF
            last_be = 0xF if length_dw > 1 else 0x0
        else:
            # Partial: use contiguous patterns
            patterns = [0x1, 0x3, 0x7, 0xF, 0xE, 0xC, 0x8]
            first_be = self.rng.choice(patterns)
            last_be = self.rng.choice(patterns) if length_dw > 1 else 0x0
        
        return first_be, last_be
    
    def random_requester_id(self) -> int:
        """Generate random requester ID."""
        c = self.constraints
        if c.vary_requester_id:
            return self.rng.randint(0, 0xFFFF)
        return c.requester_id
    
    def random_data(self, length_bytes: int) -> bytes:
        """Generate random data payload."""
        return bytes(self.rng.randint(0, 255) for _ in range(length_bytes))
    
    def random_data_pattern(self, length_bytes: int) -> bytes:
        """Generate data with recognizable patterns for debugging."""
        r = self.rng.random()
        if r < 0.3:
            # Pure random
            return self.random_data(length_bytes)
        elif r < 0.6:
            # Walking ones
            return bytes(1 << (i % 8) for i in range(length_bytes))
        else:
            # Address-tagged (byte position encoded)
            return bytes((i & 0xFF) for i in range(length_bytes))
    
    def generate_mwr_params(self) -> Dict[str, Any]:
        """Generate complete Memory Write TLP parameters."""
        length_dw = self.random_length_dw()
        first_be, last_be = self.random_byte_enables(length_dw)
        
        params = {
            'address': self.random_address(),
            'length_dw': length_dw,
            'data': self.random_data_pattern(length_dw * 4),
            'requester_id': self.random_requester_id(),
            'tag': self.random_tag(),
            'attr': self.random_attr(),
            'at': self.random_at(),
            'first_be': first_be,
            'last_be': last_be,
        }
        
        self.generated_count += 1
        self.history.append(('MWr', params.copy()))
        return params
    
    def generate_mrd_params(self) -> Dict[str, Any]:
        """Generate complete Memory Read TLP parameters."""
        length_dw = self.random_length_dw()
        first_be, last_be = self.random_byte_enables(length_dw)
        
        params = {
            'address': self.random_address(),
            'length_dw': length_dw,
            'requester_id': self.random_requester_id(),
            'tag': self.random_tag(),
            'attr': self.random_attr(),
            'at': self.random_at(),
            'first_be': first_be,
            'last_be': last_be,
        }
        
        self.generated_count += 1
        self.history.append(('MRd', params.copy()))
        return params
    
    def random_delay(self, min_cycles: int = 0, max_cycles: int = 10) -> int:
        """Generate random inter-transaction delay."""
        return self.rng.randint(min_cycles, max_cycles)


# =============================================================================
# Pre-defined Constraint Sets
# =============================================================================

BAR0_REGISTER_CONSTRAINTS = TLPConstraints(
    min_addr=0x00,
    max_addr=0xFF,
    addr_alignment=4,
    force_64bit_prob=0.0,
    min_length_dw=1,
    max_length_dw=2,
    allow_partial_be=False,
    no_snoop_prob=0.0,
    relaxed_order_prob=0.0,
    at_weights=(1.0, 0.0, 0.0, 0.0),
)

BAR1_BUFFER_CONSTRAINTS = TLPConstraints(
    min_addr=0x0000,
    max_addr=0x3FF8,  # 16KB buffer, QWORD aligned
    addr_alignment=8,
    force_64bit_prob=0.0,
    min_length_dw=1,
    max_length_dw=32,
    allow_partial_be=True,
    partial_be_prob=0.3,
    no_snoop_prob=0.0,
    at_weights=(1.0, 0.0, 0.0, 0.0),
)

MSIX_TABLE_CONSTRAINTS = TLPConstraints(
    min_addr=0x0000,
    max_addr=0x7FFF,  # 32KB table
    addr_alignment=4,
    force_64bit_prob=0.0,
    min_length_dw=1,
    max_length_dw=4,
    allow_partial_be=False,
    no_snoop_prob=0.0,
    at_weights=(1.0, 0.0, 0.0, 0.0),
)

DMA_HOST_CONSTRAINTS = TLPConstraints(
    min_addr=0x0001_0000,
    max_addr=0xFFFF_FFFF,
    addr_alignment=4,
    force_64bit_prob=0.2,
    min_length_dw=1,
    max_length_dw=128,
    allow_partial_be=True,
    no_snoop_prob=0.5,
    relaxed_order_prob=0.3,
    at_weights=(0.5, 0.0, 0.5, 0.0),
)

STRESS_TEST_CONSTRAINTS = TLPConstraints(
    min_addr=0x0000,
    max_addr=0xFFFF_FFFF,
    addr_alignment=4,
    force_64bit_prob=0.3,
    min_length_dw=1,
    max_length_dw=256,
    allow_partial_be=True,
    partial_be_prob=0.5,
    no_snoop_prob=0.5,
    relaxed_order_prob=0.5,
    at_weights=(0.25, 0.25, 0.25, 0.25),
    vary_requester_id=True,
)
