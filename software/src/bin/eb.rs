//! Etherbone CLI - Direct register access via FT601
//!
//! Usage:
//!   eb read <addr>              Read a single register
//!   eb read <addr> <count>      Read multiple registers
//!   eb write <addr> <value>     Write a single register
//!   eb probe                    Check if device responds
//!   eb dump <addr> <count>      Hex dump memory region
//!
//! Addresses and values can be specified in hex (0x...) or decimal.

use ft601::Bridge;
use std::env;
use std::process::exit;

fn parse_u32(s: &str) -> Result<u32, String> {
    if s.starts_with("0x") || s.starts_with("0X") {
        u32::from_str_radix(&s[2..], 16).map_err(|e| e.to_string())
    } else {
        s.parse().map_err(|e: std::num::ParseIntError| e.to_string())
    }
}

fn print_usage() {
    eprintln!("Etherbone CLI - Direct register access via FT601");
    eprintln!();
    eprintln!("Usage:");
    eprintln!("  eb read <addr>              Read a single 32-bit register");
    eprintln!("  eb read <addr> <count>      Read multiple consecutive registers");
    eprintln!("  eb write <addr> <value>     Write a 32-bit register");
    eprintln!("  eb probe                    Check if device responds");
    eprintln!("  eb dump <addr> <count>      Hex dump memory region");
    eprintln!("  eb list                     List available devices");
    eprintln!();
    eprintln!("Addresses and values can be hex (0x...) or decimal.");
    eprintln!();
    eprintln!("Examples:");
    eprintln!("  eb read 0x12345678");
    eprintln!("  eb write 0x12345678 0xdeadbeef");
    eprintln!("  eb dump 0x10000000 64");
}

fn cmd_list() -> Result<(), Box<dyn std::error::Error>> {
    let devices = Bridge::list_devices()?;
    if devices.is_empty() {
        println!("No FT60x devices found");
    } else {
        println!("Found {} device(s):", devices.len());
        for (i, dev) in devices.iter().enumerate() {
            println!("  [{}] {}", i, dev.description);
        }
    }
    Ok(())
}

fn cmd_probe() -> Result<(), Box<dyn std::error::Error>> {
    let bridge = Bridge::open()?;
    if bridge.probe()? {
        println!("Device responded to probe");
        Ok(())
    } else {
        eprintln!("No response to probe");
        exit(1);
    }
}

fn cmd_read(addr: u32, count: usize) -> Result<(), Box<dyn std::error::Error>> {
    let bridge = Bridge::open()?;

    if count == 1 {
        let value = bridge.read(addr)?;
        println!("0x{:08x}", value);
    } else {
        let addrs: Vec<u32> = (0..count as u32).map(|i| addr + i * 4).collect();
        let values = bridge.read_burst(&addrs)?;
        for (i, value) in values.iter().enumerate() {
            println!("0x{:08x}: 0x{:08x}", addr + (i as u32 * 4), value);
        }
    }
    Ok(())
}

fn cmd_write(addr: u32, value: u32) -> Result<(), Box<dyn std::error::Error>> {
    let bridge = Bridge::open()?;
    bridge.write(addr, value)?;
    println!("Wrote 0x{:08x} to 0x{:08x}", value, addr);
    Ok(())
}

fn cmd_dump(addr: u32, count: usize) -> Result<(), Box<dyn std::error::Error>> {
    let bridge = Bridge::open()?;

    // Read in chunks
    let addrs: Vec<u32> = (0..count as u32).map(|i| addr + i * 4).collect();
    let values = bridge.read_burst(&addrs)?;

    // Print hex dump
    for (i, chunk) in values.chunks(4).enumerate() {
        let line_addr = addr + (i as u32 * 16);
        print!("{:08x}:", line_addr);

        // Hex values
        for val in chunk {
            print!(" {:08x}", val);
        }

        // Padding if last line is short
        for _ in chunk.len()..4 {
            print!("         ");
        }

        // ASCII representation
        print!("  |");
        for val in chunk {
            for b in val.to_le_bytes() {
                if b.is_ascii_graphic() || b == b' ' {
                    print!("{}", b as char);
                } else {
                    print!(".");
                }
            }
        }
        println!("|");
    }

    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        print_usage();
        exit(1);
    }

    let result = match args[1].as_str() {
        "list" => cmd_list(),

        "probe" => cmd_probe(),

        "read" => {
            if args.len() < 3 {
                eprintln!("Error: read requires an address");
                print_usage();
                exit(1);
            }
            let addr = match parse_u32(&args[2]) {
                Ok(a) => a,
                Err(e) => {
                    eprintln!("Error: invalid address: {}", e);
                    exit(1);
                }
            };
            let count = if args.len() > 3 {
                match args[3].parse::<usize>() {
                    Ok(c) => c,
                    Err(e) => {
                        eprintln!("Error: invalid count: {}", e);
                        exit(1);
                    }
                }
            } else {
                1
            };
            cmd_read(addr, count)
        }

        "write" => {
            if args.len() < 4 {
                eprintln!("Error: write requires address and value");
                print_usage();
                exit(1);
            }
            let addr = match parse_u32(&args[2]) {
                Ok(a) => a,
                Err(e) => {
                    eprintln!("Error: invalid address: {}", e);
                    exit(1);
                }
            };
            let value = match parse_u32(&args[3]) {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("Error: invalid value: {}", e);
                    exit(1);
                }
            };
            cmd_write(addr, value)
        }

        "dump" => {
            if args.len() < 4 {
                eprintln!("Error: dump requires address and count");
                print_usage();
                exit(1);
            }
            let addr = match parse_u32(&args[2]) {
                Ok(a) => a,
                Err(e) => {
                    eprintln!("Error: invalid address: {}", e);
                    exit(1);
                }
            };
            let count = match args[3].parse::<usize>() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Error: invalid count: {}", e);
                    exit(1);
                }
            };
            cmd_dump(addr, count)
        }

        "help" | "-h" | "--help" => {
            print_usage();
            Ok(())
        }

        cmd => {
            eprintln!("Unknown command: {}", cmd);
            print_usage();
            exit(1);
        }
    };

    if let Err(e) = result {
        eprintln!("Error: {}", e);
        exit(1);
    }
}
