#
# BSA PCIe Exerciser - Functional Coverage Collection
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Functional coverage collection for PCIe TLP testing.

Tracks which parameter combinations have been exercised to identify gaps.

Usage:
    cov = CoverageCollector("my_test")
    cov.sample("address_range", "0-4K")
    cov.sample_tlp("MWr", params)
    print(cov.report())
    cov.save("coverage.json")
"""

from collections import defaultdict
from typing import Dict, Set, Any
import json


class CoverageCollector:
    """Collects functional coverage data."""
    
    def __init__(self, name: str = "default"):
        self.name = name
        self.coverpoints: Dict[str, Dict[Any, int]] = defaultdict(lambda: defaultdict(int))
        self.crosses: Dict[str, Set[tuple]] = defaultdict(set)
        
    def sample(self, coverpoint: str, value: Any):
        """Sample a single coverpoint."""
        self.coverpoints[coverpoint][value] += 1
    
    def sample_cross(self, name: str, *values):
        """Sample a cross-coverage point (multiple values together)."""
        self.crosses[name].add(tuple(values))
    
    def sample_tlp(self, tlp_type: str, params: Dict[str, Any]):
        """
        Sample all standard coverpoints for a TLP.
        
        Args:
            tlp_type: 'MWr', 'MRd', 'Cpl', etc.
            params: Dict with address, length_dw, attr, at, first_be, last_be, tag
        """
        prefix = tlp_type.lower()
        
        # Address range bins
        addr = params.get('address', 0)
        addr_bin = self._addr_to_bin(addr)
        self.sample(f"{prefix}_addr_range", addr_bin)
        
        # Length bins
        length = params.get('length_dw', 1)
        len_bin = self._length_to_bin(length)
        self.sample(f"{prefix}_length", len_bin)
        
        # Attributes
        attr = params.get('attr', 0)
        self.sample(f"{prefix}_no_snoop", bool(attr & 0x1))
        self.sample(f"{prefix}_relaxed_order", bool(attr & 0x2))
        self.sample(f"{prefix}_attr", attr)
        
        # Address Type
        at = params.get('at', 0)
        self.sample(f"{prefix}_at", at)
        
        # Byte enables
        first_be = params.get('first_be', 0xF)
        last_be = params.get('last_be', 0x0)
        self.sample(f"{prefix}_first_be", first_be)
        self.sample(f"{prefix}_last_be", last_be)
        
        # Tag ranges
        tag = params.get('tag', 0)
        tag_bin = self._tag_to_bin(tag)
        self.sample(f"{prefix}_tag_range", tag_bin)
        
        # Cross coverage
        self.sample_cross(f"{prefix}_len_x_attr", len_bin, attr)
        self.sample_cross(f"{prefix}_at_x_addr", at, addr_bin)
        self.sample_cross(f"{prefix}_be_x_len", first_be, len_bin)
    
    def _addr_to_bin(self, addr: int) -> str:
        """Bin address into ranges."""
        if addr < 0x1000:
            return "0-4K"
        elif addr < 0x1_0000:
            return "4K-64K"
        elif addr < 0x10_0000:
            return "64K-1M"
        elif addr < 0x1_0000_0000:
            return "1M-4G"
        else:
            return "4G+"
    
    def _length_to_bin(self, length_dw: int) -> str:
        """Bin length into ranges."""
        if length_dw == 1:
            return "1DW"
        elif length_dw == 2:
            return "2DW"
        elif length_dw <= 4:
            return "3-4DW"
        elif length_dw <= 16:
            return "5-16DW"
        elif length_dw <= 64:
            return "17-64DW"
        else:
            return "65+DW"
    
    def _tag_to_bin(self, tag: int) -> str:
        """Bin tag into ranges."""
        if tag < 32:
            return "0-31"
        elif tag < 128:
            return "32-127"
        else:
            return "128-255"
    
    def get_hits(self, coverpoint: str) -> int:
        """Get total hits for a coverpoint."""
        return sum(self.coverpoints[coverpoint].values())
    
    def get_bins_hit(self, coverpoint: str) -> int:
        """Get number of unique bins hit for a coverpoint."""
        return len(self.coverpoints[coverpoint])
    
    def report(self) -> str:
        """Generate human-readable coverage report."""
        lines = [
            f"=" * 60,
            f"Coverage Report: {self.name}",
            f"=" * 60,
        ]
        
        # Summary
        total_samples = sum(
            sum(v.values()) for v in self.coverpoints.values()
        )
        total_bins = sum(len(v) for v in self.coverpoints.values())
        lines.append(f"Total samples: {total_samples}, Unique bins: {total_bins}")
        lines.append("")
        
        # Coverpoints
        for cp in sorted(self.coverpoints.keys()):
            values = self.coverpoints[cp]
            total_hits = sum(values.values())
            unique_bins = len(values)
            lines.append(f"{cp}:")
            lines.append(f"  Bins: {unique_bins}, Samples: {total_hits}")
            
            # Show distribution (top values)
            sorted_vals = sorted(values.items(), key=lambda x: -x[1])
            for val, count in sorted_vals[:8]:
                pct = (count / total_hits) * 100 if total_hits > 0 else 0
                if isinstance(val, bool):
                    val_str = str(val)
                elif isinstance(val, int):
                    val_str = f"0x{val:X}" if val > 9 else str(val)
                else:
                    val_str = str(val)
                lines.append(f"    {val_str}: {count} ({pct:.1f}%)")
            
            if len(sorted_vals) > 8:
                lines.append(f"    ... and {len(sorted_vals) - 8} more")
            lines.append("")
        
        # Cross coverage
        if self.crosses:
            lines.append("Cross Coverage:")
            for name, combinations in sorted(self.crosses.items()):
                lines.append(f"  {name}: {len(combinations)} combinations")
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def save(self, filename: str):
        """Save coverage to JSON file."""
        data = {
            'name': self.name,
            'coverpoints': {k: {str(kk): vv for kk, vv in v.items()} 
                          for k, v in self.coverpoints.items()},
            'crosses': {k: [list(t) for t in v] for k, v in self.crosses.items()},
        }
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
    
    def load(self, filename: str):
        """Load coverage from JSON file (merge with existing)."""
        with open(filename) as f:
            data = json.load(f)
        
        for cp, values in data.get('coverpoints', {}).items():
            for val, count in values.items():
                # Try to convert string back to original type
                try:
                    if val in ('True', 'False'):
                        val = val == 'True'
                    elif val.startswith('0x'):
                        val = int(val, 16)
                    elif val.isdigit():
                        val = int(val)
                except:
                    pass
                self.coverpoints[cp][val] += count
        
        for name, combinations in data.get('crosses', {}).items():
            for combo in combinations:
                self.crosses[name].add(tuple(combo))
    
    def merge(self, other: 'CoverageCollector'):
        """Merge coverage from another collector."""
        for cp, values in other.coverpoints.items():
            for val, count in values.items():
                self.coverpoints[cp][val] += count
        
        for name, combinations in other.crosses.items():
            self.crosses[name].update(combinations)


# Global coverage instance for easy access
_global_coverage: CoverageCollector = None


def get_coverage(name: str = "BSA_PCIe") -> CoverageCollector:
    """Get or create global coverage collector."""
    global _global_coverage
    if _global_coverage is None:
        _global_coverage = CoverageCollector(name)
    return _global_coverage


def reset_coverage(name: str = "BSA_PCIe"):
    """Reset global coverage collector."""
    global _global_coverage
    _global_coverage = CoverageCollector(name)
