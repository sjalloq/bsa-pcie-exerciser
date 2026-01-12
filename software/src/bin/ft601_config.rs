//! FT601 chip configuration tool
//!
//! Checks and sets FT601 chip configuration for 245 FIFO mode.

use d3xx::ffi::{
    FT_60XCONFIGURATION, FT_Close, FT_Create, FT_GetChipConfiguration, FT_HANDLE,
    FT_OPEN_BY_INDEX, FT_SetChipConfiguration, FT_STATUS,
};
use std::ffi::c_void;
use std::ptr;

// Configuration constants (from FTDI documentation)
const CONFIGURATION_FIFO_MODE_245: u8 = 0;
const CONFIGURATION_FIFO_MODE_600: u8 = 1;

const CONFIGURATION_CHANNEL_CONFIG_4: u8 = 0;
const CONFIGURATION_CHANNEL_CONFIG_2: u8 = 1;
const CONFIGURATION_CHANNEL_CONFIG_1: u8 = 2;

const FIFO_CLOCK_100MHZ: u8 = 0;
const FIFO_CLOCK_66MHZ: u8 = 1;

fn fifo_mode_name(mode: u8) -> &'static str {
    match mode {
        CONFIGURATION_FIFO_MODE_245 => "245 Mode",
        CONFIGURATION_FIFO_MODE_600 => "600 Mode",
        _ => "Unknown",
    }
}

fn channel_config_name(config: u8) -> &'static str {
    match config {
        CONFIGURATION_CHANNEL_CONFIG_4 => "4 Channels",
        CONFIGURATION_CHANNEL_CONFIG_2 => "2 Channels",
        CONFIGURATION_CHANNEL_CONFIG_1 => "1 Channel",
        3 => "1 OUT Pipe",
        4 => "1 IN Pipe",
        _ => "Unknown",
    }
}

fn fifo_clock_name(clock: u8) -> &'static str {
    match clock {
        FIFO_CLOCK_100MHZ => "100 MHz",
        FIFO_CLOCK_66MHZ => "66 MHz",
        2 => "50 MHz",
        3 => "40 MHz",
        _ => "Unknown",
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("=== FT601 Chip Configuration Tool ===\n");

    // Open device using FFI directly
    let mut handle: FT_HANDLE = ptr::null_mut();
    let status = unsafe { FT_Create(0 as *mut c_void, FT_OPEN_BY_INDEX, &mut handle) };

    if status != 0 || handle.is_null() {
        return Err(format!("Failed to open device, status: {}", status).into());
    }
    println!("Device opened successfully\n");

    // Get current configuration
    let mut config: FT_60XCONFIGURATION = unsafe { std::mem::zeroed() };
    let status = unsafe {
        FT_GetChipConfiguration(handle, &mut config as *mut FT_60XCONFIGURATION as *mut c_void)
    };

    if status != 0 {
        unsafe { FT_Close(handle) };
        return Err(format!("Failed to get chip configuration, status: {}", status).into());
    }

    println!("--- Current Configuration ---");
    println!("  Vendor ID:      0x{:04x}", config.VendorID);
    println!("  Product ID:     0x{:04x}", config.ProductID);
    println!("  FIFO Mode:      {} ({})", config.FIFOMode, fifo_mode_name(config.FIFOMode));
    println!(
        "  Channel Config: {} ({})",
        config.ChannelConfig,
        channel_config_name(config.ChannelConfig)
    );
    println!(
        "  FIFO Clock:     {} ({})",
        config.FIFOClock,
        fifo_clock_name(config.FIFOClock)
    );
    println!("  Opt Features:   0x{:04x}", config.OptionalFeatureSupport);
    println!();

    // Check if configuration is correct for our use case
    let needs_update = config.FIFOMode != CONFIGURATION_FIFO_MODE_245
        || config.ChannelConfig != CONFIGURATION_CHANNEL_CONFIG_1;

    if needs_update {
        println!("--- Configuration Update Needed ---");
        println!("  Current FIFO Mode: {} (need 245 mode = {})",
                 config.FIFOMode, CONFIGURATION_FIFO_MODE_245);
        println!("  Current Channel Config: {} (need 1 Channel = {})",
                 config.ChannelConfig, CONFIGURATION_CHANNEL_CONFIG_1);
        println!();

        // Ask user if they want to update
        println!("Do you want to update the configuration? (y/n)");
        let mut input = String::new();
        std::io::stdin().read_line(&mut input)?;

        if input.trim().to_lowercase() == "y" {
            // Update configuration
            config.FIFOMode = CONFIGURATION_FIFO_MODE_245;
            config.ChannelConfig = CONFIGURATION_CHANNEL_CONFIG_1;
            // Enable notification for all channels
            config.OptionalFeatureSupport = 0x06; // DISABLECANCELSESSIONUNDERRUN | ENABLENOTIFICATIONMESSAGE_INCHALL

            println!("Updating configuration...");
            let status = unsafe {
                FT_SetChipConfiguration(
                    handle,
                    &config as *const FT_60XCONFIGURATION as *mut c_void,
                )
            };

            if status != 0 {
                println!("Failed to set configuration, status: {}", status);
            } else {
                println!("Configuration updated successfully!");
                println!("NOTE: You may need to unplug and replug the device for changes to take effect.");
            }
        } else {
            println!("Configuration not updated.");
        }
    } else {
        println!("--- Configuration OK ---");
        println!("  FIFO Mode is 245 mode");
        println!("  Channel Config is 1 Channel");
        println!("  No update needed.");
    }

    unsafe { FT_Close(handle) };
    println!("\nDevice closed.");
    Ok(())
}
