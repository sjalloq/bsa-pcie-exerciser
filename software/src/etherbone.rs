//! Etherbone protocol implementation
//!
//! Etherbone is a protocol for Wishbone bus access over various transports.
//! This module implements packet encoding/decoding for 32-bit addressing.

/// Etherbone magic number
pub const MAGIC: u16 = 0x4e6f;
/// Etherbone protocol version
pub const VERSION: u8 = 1;
/// Packet header length
pub const PACKET_HEADER_LEN: usize = 8;
/// Record header length
pub const RECORD_HEADER_LEN: usize = 4;

/// Etherbone packet
#[derive(Debug, Clone)]
pub struct Packet {
    /// Probe flag - request probe response
    pub probe: bool,
    /// Probe reply flag
    pub probe_reply: bool,
    /// Write operations: (base_addr, data_values)
    pub writes: Option<(u32, Vec<u32>)>,
    /// Read operations: (base_ret_addr, addresses)
    pub reads: Option<(u32, Vec<u32>)>,
}

impl Packet {
    /// Create a new empty packet
    pub fn new() -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: None,
            reads: None,
        }
    }

    /// Create a probe request packet
    pub fn probe_request() -> Self {
        Packet {
            probe: true,
            probe_reply: false,
            writes: None,
            reads: None,
        }
    }

    /// Create a probe reply packet
    pub fn probe_reply() -> Self {
        Packet {
            probe: false,
            probe_reply: true,
            writes: None,
            reads: None,
        }
    }

    /// Create a write packet
    pub fn write(addr: u32, data: u32) -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: Some((addr, vec![data])),
            reads: None,
        }
    }

    /// Create a multi-write packet
    pub fn write_burst(base_addr: u32, data: Vec<u32>) -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: Some((base_addr, data)),
            reads: None,
        }
    }

    /// Create a read request packet
    pub fn read(addr: u32) -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: None,
            reads: Some((0, vec![addr])),
        }
    }

    /// Create a multi-read request packet
    pub fn read_burst(addrs: Vec<u32>) -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: None,
            reads: Some((0, addrs)),
        }
    }

    /// Create a read response packet (data returned as writes)
    pub fn read_response(base_ret_addr: u32, data: Vec<u32>) -> Self {
        Packet {
            probe: false,
            probe_reply: false,
            writes: Some((base_ret_addr, data)),
            reads: None,
        }
    }

    /// Encode packet to bytes
    pub fn encode(&self) -> Vec<u8> {
        let mut buf = Vec::new();

        // Packet header (8 bytes)
        buf.extend_from_slice(&MAGIC.to_be_bytes());

        let mut flags = VERSION << 4;
        if self.probe_reply {
            flags |= 0x02;
        }
        if self.probe {
            flags |= 0x01;
        }
        buf.push(flags);
        buf.push(0x44); // addr_size=4, port_size=4

        // Padding to 8 bytes
        buf.extend_from_slice(&[0x00, 0x00, 0x00, 0x00]);

        // If probe-only, no record needed
        if self.probe || self.probe_reply {
            if self.writes.is_none() && self.reads.is_none() {
                return buf;
            }
        }

        // Record header (4 bytes)
        let wcount = self.writes.as_ref().map_or(0, |(_, d)| d.len() as u8);
        let rcount = self.reads.as_ref().map_or(0, |(_, a)| a.len() as u8);

        buf.push(0x00); // flags (bca, rca, rff, cyc, wca, wff)
        buf.push(0x0f); // byte_enable
        buf.push(wcount);
        buf.push(rcount);

        // Writes section
        if let Some((base_addr, data)) = &self.writes {
            buf.extend_from_slice(&base_addr.to_be_bytes());
            for &val in data {
                buf.extend_from_slice(&val.to_be_bytes());
            }
        }

        // Reads section
        if let Some((base_ret_addr, addrs)) = &self.reads {
            buf.extend_from_slice(&base_ret_addr.to_be_bytes());
            for &addr in addrs {
                buf.extend_from_slice(&addr.to_be_bytes());
            }
        }

        buf
    }

    /// Decode packet from bytes
    pub fn decode(data: &[u8]) -> Option<Self> {
        if data.len() < PACKET_HEADER_LEN {
            return None;
        }

        // Check magic
        let magic = u16::from_be_bytes([data[0], data[1]]);
        if magic != MAGIC {
            return None;
        }

        let flags = data[2];
        let probe_reply = (flags & 0x02) != 0;
        let probe = (flags & 0x01) != 0;

        // Probe-only packets have no record
        if data.len() == PACKET_HEADER_LEN {
            return Some(Packet {
                probe,
                probe_reply,
                writes: None,
                reads: None,
            });
        }

        if data.len() < PACKET_HEADER_LEN + RECORD_HEADER_LEN {
            return None;
        }

        // Record header
        let wcount = data[10] as usize;
        let rcount = data[11] as usize;

        let mut offset = PACKET_HEADER_LEN + RECORD_HEADER_LEN;

        // Parse writes
        let writes = if wcount > 0 {
            if data.len() < offset + 4 + (wcount * 4) {
                return None;
            }
            let base_addr = u32::from_be_bytes([
                data[offset],
                data[offset + 1],
                data[offset + 2],
                data[offset + 3],
            ]);
            offset += 4;

            let mut values = Vec::with_capacity(wcount);
            for _ in 0..wcount {
                let val = u32::from_be_bytes([
                    data[offset],
                    data[offset + 1],
                    data[offset + 2],
                    data[offset + 3],
                ]);
                values.push(val);
                offset += 4;
            }
            Some((base_addr, values))
        } else {
            None
        };

        // Parse reads
        let reads = if rcount > 0 {
            if data.len() < offset + 4 + (rcount * 4) {
                return None;
            }
            let base_ret_addr = u32::from_be_bytes([
                data[offset],
                data[offset + 1],
                data[offset + 2],
                data[offset + 3],
            ]);
            offset += 4;

            let mut addrs = Vec::with_capacity(rcount);
            for _ in 0..rcount {
                let addr = u32::from_be_bytes([
                    data[offset],
                    data[offset + 1],
                    data[offset + 2],
                    data[offset + 3],
                ]);
                addrs.push(addr);
                offset += 4;
            }
            Some((base_ret_addr, addrs))
        } else {
            None
        };

        Some(Packet {
            probe,
            probe_reply,
            writes,
            reads,
        })
    }

    /// Get write data if this is a read response
    pub fn get_read_data(&self) -> Option<&[u32]> {
        self.writes.as_ref().map(|(_, data)| data.as_slice())
    }
}

impl Default for Packet {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encode_decode_write() {
        let packet = Packet::write(0x12345678, 0xdeadbeef);
        let encoded = packet.encode();
        let decoded = Packet::decode(&encoded).unwrap();

        let (addr, data) = decoded.writes.unwrap();
        assert_eq!(addr, 0x12345678);
        assert_eq!(data, vec![0xdeadbeef]);
    }

    #[test]
    fn test_encode_decode_read() {
        let packet = Packet::read(0x12345678);
        let encoded = packet.encode();
        let decoded = Packet::decode(&encoded).unwrap();

        let (_, addrs) = decoded.reads.unwrap();
        assert_eq!(addrs, vec![0x12345678]);
    }

    #[test]
    fn test_probe() {
        let packet = Packet::probe_request();
        let encoded = packet.encode();
        let decoded = Packet::decode(&encoded).unwrap();
        assert!(decoded.probe);
    }
}
