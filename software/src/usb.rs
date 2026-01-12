//! FT601 USB driver
//!
//! Low-level USB communication with FT601 using the streaming protocol.

use d3xx::{list_devices, Device as D3xxDevice, Pipe};
use std::io::{Read, Write};
use std::time::Duration;
use std::thread;

use crate::Error;

/// USB streaming protocol preamble
pub const STREAM_PREAMBLE: u32 = 0x5aa55aa5;
/// USB streaming header size (preamble + channel + length)
pub const STREAM_HEADER_SIZE: usize = 12;

/// FT601 USB device wrapper
pub struct Device {
    inner: D3xxDevice,
}

/// Device information
#[derive(Debug, Clone)]
pub struct DeviceInfo {
    pub description: String,
    pub serial: String,
}

impl Device {
    /// List available FT60x devices
    pub fn list() -> Result<Vec<DeviceInfo>, Error> {
        let devices = list_devices().map_err(|e| Error::Usb(e.to_string()))?;
        Ok(devices
            .iter()
            .map(|d| DeviceInfo {
                description: format!("{:?}", d),
                serial: String::new(), // d3xx doesn't expose this easily
            })
            .collect())
    }

    /// Open the first available FT60x device
    pub fn open() -> Result<Self, Error> {
        let devices = list_devices().map_err(|e| Error::Usb(e.to_string()))?;

        if devices.is_empty() {
            return Err(Error::NoDevice);
        }

        let inner = devices[0].open().map_err(|e| Error::Usb(e.to_string()))?;
        Ok(Device { inner })
    }

    /// Open a specific device by index
    pub fn open_by_index(index: usize) -> Result<Self, Error> {
        let devices = list_devices().map_err(|e| Error::Usb(e.to_string()))?;

        if index >= devices.len() {
            return Err(Error::NoDevice);
        }

        let inner = devices[index]
            .open()
            .map_err(|e| Error::Usb(e.to_string()))?;
        Ok(Device { inner })
    }

    /// Send a packet with USB streaming header
    pub fn send(&self, channel: u8, payload: &[u8]) -> Result<(), Error> {
        let packet = wrap_packet(channel, payload);
        let mut pipe = self.inner.pipe(Pipe::Out0);
        pipe.set_timeout(1000); // 1 second timeout
        pipe.write_all(&packet)
            .map_err(|e| Error::Usb(e.to_string()))
    }

    /// Receive a packet, stripping the USB streaming header
    /// Returns (channel, payload) or None if no data available
    pub fn recv(&self, timeout_ms: u32) -> Result<Option<(u8, Vec<u8>)>, Error> {
        let mut buf = [0u8; 4096];
        let start = std::time::Instant::now();
        let timeout = Duration::from_millis(timeout_ms as u64);

        loop {
            let bytes_read = {
                let mut pipe = self.inner.pipe(Pipe::In0);
                match pipe.read(&mut buf) {
                    Ok(n) => n,
                    Err(e) => {
                        let msg = e.to_string();
                        if msg.contains("OperationAborted") {
                            0
                        } else {
                            return Err(Error::Usb(msg));
                        }
                    }
                }
            };

            if bytes_read > 0 {
                if let Some((channel, payload)) = unwrap_packet(&buf[..bytes_read]) {
                    return Ok(Some((channel, payload)));
                }
            }

            if start.elapsed() >= timeout {
                return Ok(None);
            }

            thread::sleep(Duration::from_micros(100));
        }
    }

    /// Send and receive - for request/response patterns
    pub fn transact(
        &self,
        channel: u8,
        request: &[u8],
        timeout_ms: u32,
    ) -> Result<Vec<u8>, Error> {
        self.send(channel, request)?;

        match self.recv(timeout_ms)? {
            Some((_ch, response)) => Ok(response),
            None => Err(Error::Timeout),
        }
    }
}

/// Wrap payload with USB streaming header
pub fn wrap_packet(channel: u8, payload: &[u8]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(STREAM_HEADER_SIZE + payload.len());
    buf.extend_from_slice(&STREAM_PREAMBLE.to_le_bytes());
    buf.extend_from_slice(&(channel as u32).to_le_bytes());
    buf.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    buf.extend_from_slice(payload);
    buf
}

/// Unwrap USB streaming packet, returning (channel, payload)
pub fn unwrap_packet(data: &[u8]) -> Option<(u8, Vec<u8>)> {
    let preamble_bytes = STREAM_PREAMBLE.to_le_bytes();

    for i in 0..data.len().saturating_sub(STREAM_HEADER_SIZE) {
        if data[i..i + 4] == preamble_bytes {
            let channel = u32::from_le_bytes([
                data[i + 4],
                data[i + 5],
                data[i + 6],
                data[i + 7],
            ]) as u8;
            let len = u32::from_le_bytes([
                data[i + 8],
                data[i + 9],
                data[i + 10],
                data[i + 11],
            ]) as usize;

            let payload_start = i + STREAM_HEADER_SIZE;
            if data.len() >= payload_start + len {
                let payload = data[payload_start..payload_start + len].to_vec();
                return Some((channel, payload));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_wrap_unwrap() {
        let payload = vec![0x01, 0x02, 0x03, 0x04];
        let wrapped = wrap_packet(0, &payload);
        let (channel, unwrapped) = unwrap_packet(&wrapped).unwrap();
        assert_eq!(channel, 0);
        assert_eq!(unwrapped, payload);
    }
}
