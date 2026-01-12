//! USB debug tool - uses notifications for receiving data

use d3xx::notification::{Notification, NotificationData};
use d3xx::{list_devices, Pipe};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn hex_dump(label: &str, data: &[u8]) {
    print!("{}: ", label);
    for (i, b) in data.iter().enumerate() {
        print!("{:02x}", b);
        if (i + 1) % 4 == 0 {
            print!(" ");
        }
    }
    println!();
}

// Shared state for the notification callback
struct SharedState {
    data_ready: AtomicBool,
    received_data: Mutex<Vec<u8>>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("=== USB Debug Tool (Notification-based) ===\n");

    // List devices
    let devices = list_devices()?;
    if devices.is_empty() {
        return Err("No FT60x devices found".into());
    }
    println!("Found {} device(s)", devices.len());

    // Open device
    let device = devices[0].open()?;
    println!("Device opened\n");

    // Check device descriptor
    println!("--- Device Descriptor ---");
    match device.device_descriptor() {
        Ok(desc) => {
            println!("  Vendor ID: 0x{:04x}", desc.vendor_id());
            println!("  Product ID: 0x{:04x}", desc.product_id());
        }
        Err(e) => {
            println!("  Failed to read descriptor: {:?}", e);
        }
    }
    println!();

    // Wrap device in Arc<Mutex> for notification callback
    let device = Arc::new(Mutex::new(device));
    let shared_state = Arc::new(SharedState {
        data_ready: AtomicBool::new(false),
        received_data: Mutex::new(Vec::new()),
    });

    // Setup notification callback
    let state_clone = shared_state.clone();
    let device_clone = device.clone();
    {
        let device_lock = device.lock().unwrap();
        device_lock.set_notification_callback(
            move |notification: Notification<()>| {
                match notification.data() {
                    NotificationData::Data { endpoint, size } => {
                        println!("  [Notification] Data on {:?}, {} bytes", endpoint, size);

                        // Read the data
                        let dev = device_clone.lock().unwrap();
                        let mut buf = vec![0u8; *size];
                        match dev.pipe(*endpoint).read(&mut buf) {
                            Ok(n) => {
                                let mut data = state_clone.received_data.lock().unwrap();
                                data.extend_from_slice(&buf[..n]);
                                state_clone.data_ready.store(true, Ordering::SeqCst);
                                hex_dump("  [Notification] Data", &buf[..n]);
                            }
                            Err(e) => {
                                println!("  [Notification] Read error: {}", e);
                            }
                        }
                    }
                    NotificationData::Gpio { gpio0, gpio1 } => {
                        println!("  [Notification] GPIO: {} {}", gpio0, gpio1);
                    }
                }
            },
            None,
        )?;
    }
    println!("Notification callback set\n");

    // Build USB packet
    let preamble: u32 = 0x5aa55aa5;
    let channel: u32 = 0;
    let eb_packet: [u8; 8] = [
        0x4e, 0x6f, // Magic (big-endian)
        0x11,       // Flags: ver=1, probe=1
        0x44,       // addr_size=4, port_size=4
        0x00, 0x00, 0x00, 0x00, // Padding
    ];
    let length: u32 = eb_packet.len() as u32;

    let mut usb_packet = Vec::new();
    usb_packet.extend_from_slice(&preamble.to_le_bytes());
    usb_packet.extend_from_slice(&channel.to_le_bytes());
    usb_packet.extend_from_slice(&length.to_le_bytes());
    usb_packet.extend_from_slice(&eb_packet);

    println!("--- USB Packet ---");
    hex_dump("  Full packet", &usb_packet);
    println!("  Total: {} bytes\n", usb_packet.len());

    // Send packet
    println!("--- Sending ---");
    {
        let dev = device.lock().unwrap();
        let mut out_pipe = dev.pipe(Pipe::Out0);
        let _ = out_pipe.set_timeout(5000);
        match out_pipe.write(&usb_packet) {
            Ok(n) => println!("  Wrote {} bytes OK", n),
            Err(e) => {
                println!("  Write error: {}", e);
                return Err(e.into());
            }
        }
    }

    // Wait for notification
    println!("\n--- Waiting for notification (3 seconds max) ---");
    let start = Instant::now();
    let timeout = Duration::from_secs(3);

    while start.elapsed() < timeout {
        if shared_state.data_ready.load(Ordering::SeqCst) {
            println!("  Data received via notification!");
            let data = shared_state.received_data.lock().unwrap();
            hex_dump("  Received", &data);
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }

    println!("  No notification received within timeout");

    // Try a direct read anyway
    println!("\n--- Trying direct read (fallback) ---");
    {
        let dev = device.lock().unwrap();
        let mut in_pipe = dev.pipe(Pipe::In0);
        let _ = in_pipe.set_timeout(100); // Very short timeout

        let mut buf = [0u8; 512];
        match in_pipe.read(&mut buf) {
            Ok(n) if n > 0 => {
                println!("  Direct read: {} bytes", n);
                hex_dump("  Data", &buf[..n]);
            }
            Ok(_) => println!("  Direct read: 0 bytes"),
            Err(e) => println!("  Direct read error: {}", e),
        }
    }

    println!("\nDone");
    Ok(())
}
