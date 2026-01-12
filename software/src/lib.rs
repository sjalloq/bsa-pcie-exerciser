//! FT601 USB Bridge Library
//!
//! Provides USB communication with FT601 devices and Etherbone protocol
//! support for Wishbone bus access.
//!
//! # Example
//!
//! ```no_run
//! use ft601::Bridge;
//!
//! let bridge = Bridge::open().unwrap();
//!
//! // Read a register
//! let value = bridge.read(0x12345678).unwrap();
//! println!("Read: 0x{:08x}", value);
//!
//! // Write a register
//! bridge.write(0x12345678, 0xdeadbeef).unwrap();
//! ```

pub mod etherbone;
pub mod usb;

use std::sync::Mutex;

/// Library error types
#[derive(Debug)]
pub enum Error {
    /// No FT60x device found
    NoDevice,
    /// USB communication error
    Usb(String),
    /// Timeout waiting for response
    Timeout,
    /// Protocol error
    Protocol(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::NoDevice => write!(f, "No FT60x device found"),
            Error::Usb(s) => write!(f, "USB error: {}", s),
            Error::Timeout => write!(f, "Timeout waiting for response"),
            Error::Protocol(s) => write!(f, "Protocol error: {}", s),
        }
    }
}

impl std::error::Error for Error {}

/// High-level bridge for Wishbone register access via FT601
pub struct Bridge {
    device: Mutex<usb::Device>,
    /// USB channel for Etherbone (default: 0)
    pub channel: u8,
    /// Timeout in milliseconds for read operations
    pub timeout_ms: u32,
}

impl Bridge {
    /// Open the first available FT601 device
    pub fn open() -> Result<Self, Error> {
        let device = usb::Device::open()?;
        Ok(Bridge {
            device: Mutex::new(device),
            channel: 0,
            timeout_ms: 100,
        })
    }

    /// Open a specific device by index
    pub fn open_by_index(index: usize) -> Result<Self, Error> {
        let device = usb::Device::open_by_index(index)?;
        Ok(Bridge {
            device: Mutex::new(device),
            channel: 0,
            timeout_ms: 100,
        })
    }

    /// List available devices
    pub fn list_devices() -> Result<Vec<usb::DeviceInfo>, Error> {
        usb::Device::list()
    }

    /// Read a 32-bit register
    pub fn read(&self, addr: u32) -> Result<u32, Error> {
        let packet = etherbone::Packet::read(addr);
        let request = packet.encode();

        let device = self.device.lock().unwrap();
        let response = device.transact(self.channel, &request, self.timeout_ms)?;

        let resp_packet = etherbone::Packet::decode(&response)
            .ok_or_else(|| Error::Protocol("Invalid response packet".into()))?;

        resp_packet
            .get_read_data()
            .and_then(|d| d.first().copied())
            .ok_or_else(|| Error::Protocol("No data in response".into()))
    }

    /// Read multiple 32-bit registers
    pub fn read_burst(&self, addrs: &[u32]) -> Result<Vec<u32>, Error> {
        let packet = etherbone::Packet::read_burst(addrs.to_vec());
        let request = packet.encode();

        let device = self.device.lock().unwrap();
        let response = device.transact(self.channel, &request, self.timeout_ms)?;

        let resp_packet = etherbone::Packet::decode(&response)
            .ok_or_else(|| Error::Protocol("Invalid response packet".into()))?;

        resp_packet
            .get_read_data()
            .map(|d| d.to_vec())
            .ok_or_else(|| Error::Protocol("No data in response".into()))
    }

    /// Write a 32-bit register
    pub fn write(&self, addr: u32, value: u32) -> Result<(), Error> {
        let packet = etherbone::Packet::write(addr, value);
        let request = packet.encode();

        let device = self.device.lock().unwrap();
        device.send(self.channel, &request)
    }

    /// Write multiple 32-bit values starting at base address
    pub fn write_burst(&self, base_addr: u32, values: &[u32]) -> Result<(), Error> {
        let packet = etherbone::Packet::write_burst(base_addr, values.to_vec());
        let request = packet.encode();

        let device = self.device.lock().unwrap();
        device.send(self.channel, &request)
    }

    /// Send a probe request and wait for reply
    pub fn probe(&self) -> Result<bool, Error> {
        let packet = etherbone::Packet::probe_request();
        let request = packet.encode();

        let device = self.device.lock().unwrap();
        match device.transact(self.channel, &request, self.timeout_ms) {
            Ok(response) => {
                if let Some(resp_packet) = etherbone::Packet::decode(&response) {
                    Ok(resp_packet.probe_reply)
                } else {
                    Ok(false)
                }
            }
            Err(Error::Timeout) => Ok(false),
            Err(e) => Err(e),
        }
    }
}

// Re-exports for convenience
pub use etherbone::Packet as EtherbonePacket;
pub use usb::Device as UsbDevice;
