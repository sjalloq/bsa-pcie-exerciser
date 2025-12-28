#
# BSA PCIe Exerciser - TLP Builder
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
TLP construction helpers for integration tests.

Builds TLP packets in the format expected by LitePCIe's 64-bit PHY interface.
For 64-bit data width, DW0 goes in the LOWER 32 bits of each beat.

The LitePCIeTLPHeaderExtracter64b expects:
  - sink.dat[31:0]  = DW at lower address (DW0 for first beat)
  - sink.dat[63:32] = DW at higher address (DW1 for first beat)

PCIe TLP Header Formats:
- 3DW Header (32-bit address): Used for Memory Read/Write with address < 4GB
- 4DW Header (64-bit address): Used for Memory Read/Write with address >= 4GB

TLP Type encodings (Fmt[2:0] | Type[4:0]):
- Memory Read 32:  Fmt=000, Type=00000 -> 0x00
- Memory Read 64:  Fmt=001, Type=00000 -> 0x20
- Memory Write 32: Fmt=010, Type=00000 -> 0x40
- Memory Write 64: Fmt=011, Type=00000 -> 0x60
- Completion:      Fmt=000, Type=01010 -> 0x0A
- Completion w/D:  Fmt=010, Type=01010 -> 0x4A
"""


class TLPBuilder:
    """Helper class for building TLP packets."""

    @staticmethod
    def memory_write_32(address, data_bytes, requester_id=0x0100, tag=0, attr=0, at=0):
        """
        Build 32-bit Memory Write TLP.

        Args:
            address: 32-bit target address (must be DWORD-aligned)
            data_bytes: bytes object with data to write
            requester_id: 16-bit requester ID
            tag: 8-bit tag
            attr: 2-bit attribute field [1]=Relaxed Ordering, [0]=No Snoop
            at: 2-bit address type (0=untranslated, 1=trans req, 2=translated)

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        length = (len(data_bytes) + 3) // 4  # Length in DWORDs

        # DW0: Fmt=010 (3DW+data), Type=00000 (MWr), Attr[13:12], AT[11:10], Length[9:0]
        dw0 = ((0b010 << 29) | (0b00000 << 24) |
               ((attr & 0x3) << 12) | ((at & 0x3) << 10) | (length & 0x3FF))

        # DW1: Requester ID, Tag, Last BE, First BE
        first_be = 0xF
        last_be = 0xF if length > 1 else 0x0
        dw1 = (requester_id << 16) | (tag << 8) | (last_be << 4) | first_be

        # DW2: Address (lower 2 bits must be 0)
        dw2 = address & 0xFFFFFFFC

        beats = []

        # Beat 0: DW0 (lower), DW1 (upper) - LitePCIe expects DW0 in lower 32 bits
        beats.append({'dat': (dw1 << 32) | dw0, 'be': 0xFF})

        # Pad data to DWORD boundary
        padded_data = data_bytes + b'\x00' * (4 - len(data_bytes) % 4) if len(data_bytes) % 4 else data_bytes

        # Beat 1: DW2 (lower), first data DWORD (upper)
        # Use 'big' to put data in big-endian wire format (depacketizer will byte-swap to little-endian)
        data_dw0 = int.from_bytes(padded_data[0:4], 'big')
        beats.append({'dat': (data_dw0 << 32) | dw2, 'be': 0xFF})

        # Additional data beats as needed
        for i in range(4, len(padded_data), 8):
            dw_a = int.from_bytes(padded_data[i:i+4], 'big') if i < len(padded_data) else 0
            dw_b = int.from_bytes(padded_data[i+4:i+8], 'big') if i+4 < len(padded_data) else 0
            be = 0xFF
            if i + 8 > len(padded_data):
                # Partial last beat - lower DWORD only
                remaining = len(padded_data) - i
                if remaining <= 4:
                    be = 0x0F  # Only lower 4 bytes valid
            # Lower address DWORD in [31:0], higher in [63:32]
            beats.append({'dat': (dw_b << 32) | dw_a, 'be': be})

        return beats

    @staticmethod
    def memory_read_32(address, length_dw, requester_id=0x0100, tag=0, attr=0, at=0):
        """
        Build 32-bit Memory Read TLP.

        Args:
            address: 32-bit target address (must be DWORD-aligned)
            length_dw: Length in DWORDs to read
            requester_id: 16-bit requester ID
            tag: 8-bit tag
            attr: 2-bit attribute field [1]=Relaxed Ordering, [0]=No Snoop
            at: 2-bit address type (0=untranslated, 1=trans req, 2=translated)

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        # DW0: Fmt=00 (3DW, no data), Type=00000 (MRd), Attr[13:12], AT[11:10], Length[9:0]
        dw0 = ((0b000 << 29) | (0b00000 << 24) |
               ((attr & 0x3) << 12) | ((at & 0x3) << 10) | (length_dw & 0x3FF))

        first_be = 0xF
        last_be = 0xF if length_dw > 1 else 0x0
        dw1 = (requester_id << 16) | (tag << 8) | (last_be << 4) | first_be

        dw2 = address & 0xFFFFFFFC

        # LitePCIe expects DW0 in lower 32 bits
        return [
            {'dat': (dw1 << 32) | dw0, 'be': 0xFF},      # DW0 lower, DW1 upper
            {'dat': (0 << 32) | dw2, 'be': 0x0F},        # DW2 lower, only lower 4 bytes valid
        ]

    @staticmethod
    def completion(requester_id, completer_id, tag, data_bytes, status=0, lower_addr=0):
        """
        Build Completion with Data TLP.

        Args:
            requester_id: 16-bit requester ID (who requested)
            completer_id: 16-bit completer ID (who is responding)
            tag: 8-bit tag from original request
            data_bytes: bytes object with completion data
            status: Completion status (0=SC, 1=UR, 2=CRS, 4=CA)
            lower_addr: Lower 7 bits of byte address

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        length = (len(data_bytes) + 3) // 4  # Length in DWORDs
        byte_count = len(data_bytes)

        # DW0: Fmt=010 (3DW+data), Type=01010 (CplD)
        dw0 = (0b010 << 29) | (0b01010 << 24) | (length & 0x3FF)

        # DW1: Completer ID, Status, BCM, Byte Count
        dw1 = (completer_id << 16) | (status << 13) | (byte_count & 0xFFF)

        # DW2: Requester ID, Tag, Lower Address
        dw2 = (requester_id << 16) | (tag << 8) | (lower_addr & 0x7F)

        beats = []
        # Beat 0: DW0 (lower), DW1 (upper) - LitePCIe expects DW0 in lower 32 bits
        beats.append({'dat': (dw1 << 32) | dw0, 'be': 0xFF})

        # Pad data to DWORD boundary
        padded_data = data_bytes + b'\x00' * (4 - len(data_bytes) % 4) if len(data_bytes) % 4 else data_bytes

        # Beat 1: DW2 (lower), first data DWORD (upper)
        # Use 'big' to put data in big-endian wire format (depacketizer will byte-swap to little-endian)
        data_dw0 = int.from_bytes(padded_data[0:4], 'big')
        beats.append({'dat': (data_dw0 << 32) | dw2, 'be': 0xFF})

        # Additional data beats as needed
        for i in range(4, len(padded_data), 8):
            dw_a = int.from_bytes(padded_data[i:i+4], 'big') if i < len(padded_data) else 0
            dw_b = int.from_bytes(padded_data[i+4:i+8], 'big') if i+4 < len(padded_data) else 0
            # Lower address DWORD in [31:0], higher in [63:32]
            beats.append({'dat': (dw_b << 32) | dw_a, 'be': 0xFF})

        return beats

    @staticmethod
    def ats_translation_completion(requester_id, completer_id, tag,
                                   translated_addr, s_field=0, permissions=0x3F):
        """
        Build ATS Translation Completion TLP.

        Args:
            requester_id: 16-bit requester ID (who requested translation)
            completer_id: 16-bit completer ID (IOMMU/SMMU)
            tag: 8-bit tag from translation request
            translated_addr: Translated physical address (page-aligned)
            s_field: Size field (0=4KB, 1=8KB, etc.)
            permissions: Permission bits (R, W, Priv, etc.)

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        # ATS Translation Completion is a CplD with 2 DWORDs of data
        # Data format: [TranslatedAddr63:12 | S | N | U | R | W | P | 0 | 0]
        #              [TranslatedAddr11:0 (always 0 for page) | Reserved]

        # Build translation data (8 bytes)
        # Lower DWORD: [Addr[31:12] | S[4:0] | N | U | R | W | Priv | Reserved]
        # Upper DWORD: [Addr[63:32]]

        # Simplified: just put the translated address with permissions
        lower_dw = ((translated_addr & 0xFFFFF000) |
                    ((s_field & 0x1F) << 7) |
                    ((permissions & 0x3F) << 1))
        upper_dw = (translated_addr >> 32) & 0xFFFFFFFF

        # Pack as 8 bytes (little endian for LitePCIe data path)
        data_bytes = lower_dw.to_bytes(4, 'little') + upper_dw.to_bytes(4, 'little')

        return TLPBuilder.completion(requester_id, completer_id, tag, data_bytes)

    @staticmethod
    def extract_address_from_mwr(beats):
        """
        Extract target address from a Memory Write TLP.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Address from the TLP header

        Note: At PHY level, headers are in big-endian format where bit positions
        match HeaderField definitions directly. No byte-swap needed for headers.
        """
        if not beats:
            return None

        # LitePCIe format: DW0 in lower 32 bits
        # Beat 0: [DW1 | DW0] (DW0 in lower, DW1 in upper)
        # Beat 1: [Data0 | DW2] for 3DW header
        # Beat 1: [DW3 | DW2] for 4DW header
        # Headers use big-endian format - bit positions match HeaderField definitions

        dw0 = beats[0]['dat'] & 0xFFFFFFFF
        fmt = (dw0 >> 29) & 0x7

        if fmt in (0b010, 0b000):  # 3DW header
            dw2 = beats[1]['dat'] & 0xFFFFFFFF  # DW2 in lower
            return dw2 & 0xFFFFFFFC
        elif fmt in (0b011, 0b001):  # 4DW header
            dw2 = beats[1]['dat'] & 0xFFFFFFFF  # DW2 in lower (addr high)
            dw3 = (beats[1]['dat'] >> 32) & 0xFFFFFFFF  # DW3 in upper (addr low)
            return ((dw2 << 32) | dw3) & 0xFFFFFFFFFFFFFFFC
        else:
            return None

    @staticmethod
    def extract_tag_from_cpl(beats):
        """
        Extract tag from a Completion TLP.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Tag value from the completion header

        Note: At PHY level, headers are in big-endian format where bit positions
        match HeaderField definitions directly. No byte-swap needed for headers.
        """
        if not beats or len(beats) < 2:
            return None

        # LitePCIe format: DW2 is in lower 32 bits of beat 1
        # Tag is in bits [15:8] of DW2
        # Headers use big-endian format - bit positions match HeaderField definitions
        dw2 = beats[1]['dat'] & 0xFFFFFFFF
        return (dw2 >> 8) & 0xFF

    @staticmethod
    def memory_write_64(address, data_bytes, requester_id=0x0100, tag=0):
        """
        Build 64-bit Memory Write TLP (4DW header for addresses >= 4GB).

        Args:
            address: 64-bit target address (must be DWORD-aligned)
            data_bytes: bytes object with data to write
            requester_id: 16-bit requester ID
            tag: 8-bit tag

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        length = (len(data_bytes) + 3) // 4  # Length in DWORDs

        # DW0: Fmt=011 (4DW+data), Type=00000 (MWr)
        dw0 = (0b011 << 29) | (0b00000 << 24) | (length & 0x3FF)

        # DW1: Requester ID, Tag, Last BE, First BE
        first_be = 0xF
        last_be = 0xF if length > 1 else 0x0
        dw1 = (requester_id << 16) | (tag << 8) | (last_be << 4) | first_be

        # DW2: Address high (bits [63:32])
        dw2 = (address >> 32) & 0xFFFFFFFF

        # DW3: Address low (bits [31:2], lower 2 bits must be 0)
        dw3 = address & 0xFFFFFFFC

        beats = []

        # Beat 0: DW0 (lower), DW1 (upper)
        beats.append({'dat': (dw1 << 32) | dw0, 'be': 0xFF})

        # Beat 1: DW2 (lower), DW3 (upper)
        beats.append({'dat': (dw3 << 32) | dw2, 'be': 0xFF})

        # Pad data to DWORD boundary
        padded_data = data_bytes + b'\x00' * (4 - len(data_bytes) % 4) if len(data_bytes) % 4 else data_bytes

        # Beat 2+: Data DWORDs
        for i in range(0, len(padded_data), 8):
            dw_a = int.from_bytes(padded_data[i:i+4], 'big') if i < len(padded_data) else 0
            dw_b = int.from_bytes(padded_data[i+4:i+8], 'big') if i+4 < len(padded_data) else 0
            be = 0xFF
            if i + 8 > len(padded_data):
                remaining = len(padded_data) - i
                if remaining <= 4:
                    be = 0x0F  # Only lower 4 bytes valid
            beats.append({'dat': (dw_b << 32) | dw_a, 'be': be})

        return beats

    @staticmethod
    def memory_read_64(address, length_dw, requester_id=0x0100, tag=0):
        """
        Build 64-bit Memory Read TLP (4DW header for addresses >= 4GB).

        Args:
            address: 64-bit target address (must be DWORD-aligned)
            length_dw: Length in DWORDs to read
            requester_id: 16-bit requester ID
            tag: 8-bit tag

        Returns:
            List of beat dicts with 'dat' and 'be' keys
        """
        # DW0: Fmt=001 (4DW, no data), Type=00000 (MRd)
        dw0 = (0b001 << 29) | (0b00000 << 24) | (length_dw & 0x3FF)

        first_be = 0xF
        last_be = 0xF if length_dw > 1 else 0x0
        dw1 = (requester_id << 16) | (tag << 8) | (last_be << 4) | first_be

        # DW2: Address high (bits [63:32])
        dw2 = (address >> 32) & 0xFFFFFFFF

        # DW3: Address low (bits [31:2])
        dw3 = address & 0xFFFFFFFC

        return [
            {'dat': (dw1 << 32) | dw0, 'be': 0xFF},      # DW0 lower, DW1 upper
            {'dat': (dw3 << 32) | dw2, 'be': 0xFF},      # DW2 lower, DW3 upper
        ]

    @staticmethod
    def extract_pasid_from_tlp(beats):
        """
        Extract PASID prefix information from a TLP if present.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Tuple of (has_pasid, pasid_val, privileged, execute) if PASID prefix present,
            or (False, 0, False, False) if no PASID prefix.

        PASID TLP Prefix format (E2E Type 0x91):
            [31:24] = 0x91 (E2E TLP Prefix Type)
            [23:22] = Reserved
            [21]    = PMR (Privileged Mode Requested)
            [20]    = Execute Requested
            [19:0]  = PASID value
        """
        if not beats:
            return (False, 0, False, False)

        # Check first DWORD for PASID prefix (type 0x91)
        dw0 = beats[0]['dat'] & 0xFFFFFFFF
        prefix_type = (dw0 >> 24) & 0xFF

        if prefix_type == 0x91:
            # Extract PASID fields
            pasid_val = dw0 & 0xFFFFF           # bits [19:0]
            execute = bool((dw0 >> 20) & 0x1)   # bit [20]
            privileged = bool((dw0 >> 21) & 0x1)  # bit [21]
            return (True, pasid_val, privileged, execute)
        else:
            return (False, 0, False, False)

    @staticmethod
    def extract_tlp_type(beats):
        """
        Extract TLP type information, handling PASID prefix if present.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Tuple of (fmt, tlp_type, has_pasid) where:
            - fmt: 3-bit format field
            - tlp_type: 5-bit type field
            - has_pasid: True if PASID prefix present
        """
        if not beats:
            return (0, 0, False)

        dw0 = beats[0]['dat'] & 0xFFFFFFFF
        has_pasid = ((dw0 >> 24) & 0xFF) == 0x91

        if has_pasid:
            # With PASID prefix, actual TLP header starts in upper 32 bits
            header_dw0 = (beats[0]['dat'] >> 32) & 0xFFFFFFFF
        else:
            header_dw0 = dw0

        fmt = (header_dw0 >> 29) & 0x7
        tlp_type = (header_dw0 >> 24) & 0x1F

        return (fmt, tlp_type, has_pasid)

    @staticmethod
    def extract_attr_from_tlp(beats):
        """
        Extract TLP attributes (No-Snoop, Relaxed Ordering, AT) from header.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Tuple of (attr, at) where:
            - attr: 2-bit attribute field [1]=Relaxed Ordering, [0]=No Snoop
            - at: 2-bit address type field
        """
        if not beats:
            return (0, 0)

        dw0 = beats[0]['dat'] & 0xFFFFFFFF
        attr = (dw0 >> 12) & 0x3  # bits [13:12]
        at = (dw0 >> 10) & 0x3    # bits [11:10]
        return (attr, at)

    @staticmethod
    def extract_tag_from_mrd(beats):
        """
        Extract tag from a Memory Read TLP.

        Args:
            beats: List of beat dicts from captured TLP

        Returns:
            Tag value from the TLP header, or None if beats is empty.
        """
        if not beats:
            return None
        # DW1 is in upper 32 bits of beat 0
        dw1 = (beats[0]['dat'] >> 32) & 0xFFFFFFFF
        return (dw1 >> 8) & 0xFF
