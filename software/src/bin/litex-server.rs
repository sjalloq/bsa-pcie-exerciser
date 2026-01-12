//! LiteX Server for FT601 USB Bridge
//!
//! TCP server compatible with litex_cli, LiteScope, and RemoteClient.
//! Bridges Etherbone protocol over TCP to FT601 USB.

use ft601::{etherbone, usb, Error};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use log::{debug, error, info, warn};

/// Server configuration
struct Config {
    bind_addr: String,
    port: u16,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            bind_addr: "0.0.0.0".into(),
            port: 1234,
        }
    }
}

fn handle_client(
    mut stream: TcpStream,
    device: Arc<Mutex<usb::Device>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let peer = stream.peer_addr()?;
    info!("Client connected: {}", peer);

    // Send server info (RemoteClient expects this)
    let info = "CommFT601:localhost:1234";
    stream.write_all(info.as_bytes())?;

    stream.set_read_timeout(Some(Duration::from_secs(2)))?;

    let mut buf = [0u8; 4096];
    loop {
        // Read from client
        let n = match stream.read(&mut buf) {
            Ok(0) => {
                info!("Client disconnected: {}", peer);
                break;
            }
            Ok(n) => n,
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => continue,
            Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => continue,
            Err(e) => {
                error!("Read error: {}", e);
                break;
            }
        };

        debug!("Received {} bytes from client", n);

        // Decode Etherbone packet
        let packet = match etherbone::Packet::decode(&buf[..n]) {
            Some(p) => p,
            None => {
                warn!("Failed to decode Etherbone packet");
                continue;
            }
        };

        // Handle probe
        if packet.probe {
            debug!("Probe request, sending reply");
            let response = etherbone::Packet::probe_reply().encode();
            stream.write_all(&response)?;
            continue;
        }

        let device = device.lock().unwrap();

        // Handle writes
        if let Some((base_addr, data)) = &packet.writes {
            debug!("Write {} values @ 0x{:08x}", data.len(), base_addr);
            let write_packet = etherbone::Packet::write_burst(*base_addr, data.clone());
            if let Err(e) = device.send(0, &write_packet.encode()) {
                error!("Write error: {}", e);
            }
        }

        // Handle reads
        if let Some((base_ret_addr, addrs)) = &packet.reads {
            debug!("Read {} addresses", addrs.len());

            let mut results = Vec::new();
            for &addr in addrs {
                let read_packet = etherbone::Packet::read(addr);
                match device.transact(0, &read_packet.encode(), 100) {
                    Ok(response) => {
                        if let Some(resp) = etherbone::Packet::decode(&response) {
                            if let Some(data) = resp.get_read_data() {
                                if let Some(&val) = data.first() {
                                    debug!("  0x{:08x} -> 0x{:08x}", addr, val);
                                    results.push(val);
                                    continue;
                                }
                            }
                        }
                        warn!("Invalid response for read @ 0x{:08x}", addr);
                        results.push(0xffffffff);
                    }
                    Err(Error::Timeout) => {
                        warn!("Timeout reading @ 0x{:08x}", addr);
                        results.push(0xffffffff);
                    }
                    Err(e) => {
                        error!("Read error @ 0x{:08x}: {}", addr, e);
                        results.push(0xffffffff);
                    }
                }
            }

            // Send response
            let response = etherbone::Packet::read_response(*base_ret_addr, results);
            stream.write_all(&response.encode())?;
        }
    }

    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let config = Config::default();

    info!("LiteX FT601 Server");
    info!("==================");

    // List and open device
    let devices = usb::Device::list()?;
    if devices.is_empty() {
        error!("No FT60x devices found!");
        return Err("No FT60x devices found".into());
    }

    info!("Found {} device(s):", devices.len());
    for (i, dev) in devices.iter().enumerate() {
        info!("  [{}] {}", i, dev.description);
    }

    info!("Opening device 0...");
    let device = Arc::new(Mutex::new(usb::Device::open()?));
    info!("Device opened successfully");

    // Start TCP server
    let bind = format!("{}:{}", config.bind_addr, config.port);
    let listener = TcpListener::bind(&bind)?;
    info!("Listening on {}", bind);
    info!("");
    info!("Ready for connections from litex_cli, LiteScope, etc.");

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let device = Arc::clone(&device);
                thread::spawn(move || {
                    if let Err(e) = handle_client(stream, device) {
                        error!("Client handler error: {}", e);
                    }
                });
            }
            Err(e) => {
                error!("Accept error: {}", e);
            }
        }
    }

    Ok(())
}
