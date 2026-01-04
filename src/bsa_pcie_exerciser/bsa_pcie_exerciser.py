#!/usr/bin/env python3
#
# BSA PCIe Exerciser - Build Script
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

import os

import rich_click as click
from litex.soc.integration.builder import Builder


# =============================================================================
# Platform and CRG Configuration
# =============================================================================

PLATFORMS = {
    "spec_a7": {
        "description": "SPEC-A7 (XC7A35T)",
        "platform_module": "bsa_pcie_exerciser.gateware.platform.spec_a7",
        "crg_module": "bsa_pcie_exerciser.gateware.soc.spec_a7",
        "crg_class": "SPECA7CRG",
        "variant": "xc7a35t",
        "sys_clk_freq": 125e6,
    },
    "squirrel": {
        "description": "Squirrel/CaptainDMA (XC7A35T)",
        "platform_module": "bsa_pcie_exerciser.gateware.platform.squirrel",
        "crg_module": "bsa_pcie_exerciser.gateware.soc.squirrel",
        "crg_class": "SquirrelCRG",
        "variant": "xc7a35t",
        "sys_clk_freq": 125e6,
    },
}


def get_platform_config(platform_name):
    """
    Get platform configuration and dynamically import classes.

    Args:
        platform_name: Key from PLATFORMS dict

    Returns:
        dict with Platform class, CRG class, and config
    """
    if platform_name not in PLATFORMS:
        raise click.ClickException(
            f"Unknown platform: {platform_name}. "
            f"Available: {', '.join(PLATFORMS.keys())}"
        )

    config = PLATFORMS[platform_name]

    # Import platform module
    platform_mod = __import__(config["platform_module"], fromlist=["Platform"])
    config["Platform"] = platform_mod.Platform

    # Import CRG class
    crg_mod = __import__(config["crg_module"], fromlist=[config["crg_class"]])
    config["CRG"] = getattr(crg_mod, config["crg_class"])

    return config


def get_bitstream_path(platform, output_dir, mode="bit"):
    """Get path to bitstream file.

    Args:
        platform: Platform name
        output_dir: Build output directory
        mode: "bit" for SRAM, "bin" for flash

    Returns:
        Path to bitstream file
    """
    build_dir = output_dir or f"build/{platform}"
    ext = "bit" if mode == "bit" else "bin"
    return os.path.join(build_dir, "gateware", f"{platform}.{ext}")


# =============================================================================
# CLI
# =============================================================================

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
def cli():
    """BSA PCIe Exerciser CLI Tool

    Build, load, and flash BSA PCIe Exerciser gateware to supported FPGA boards.
    """
    pass


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--platform", "-p", type=click.Choice(list(PLATFORMS.keys()), case_sensitive=False), default="squirrel", show_default=True, help="Target platform")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Build output directory (default: build/<platform>)")
def build(platform, output_dir):
    """Build gateware for the specified platform."""

    # Get platform configuration
    config = get_platform_config(platform)

    # Create platform
    platform_inst = config["Platform"](variant=config["variant"])

    # Select SoC based on platform
    if platform == "squirrel":
        from bsa_pcie_exerciser.gateware.soc.squirrel import SquirrelSoC
        SoC = SquirrelSoC
    elif platform == "spec_a7":
        from bsa_pcie_exerciser.gateware.soc.spec_a7 import SPECA7SoC
        SoC = SPECA7SoC
    else:
        raise click.ClickException(f"No SoC defined for platform: {platform}")

    click.echo(f"Building for [bold green]{platform}[/] ({config['variant']})...")

    # Create SoC (each platform SoC handles its own CRG)
    soc = SoC(
        platform_inst,
        sys_clk_freq=int(config["sys_clk_freq"]),
    )

    # Build
    build_output_dir = output_dir or f"build/{platform}"
    builder = Builder(soc, output_dir=build_output_dir)
    builder.build()

    click.echo(f"[bold green]Build complete![/] Output: {build_output_dir}")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--platform", "-p", type=click.Choice(list(PLATFORMS.keys()), case_sensitive=False), default="squirrel", show_default=True, help="Target platform")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Build output directory (default: build/<platform>)")
@click.option("--bitstream", "-b", type=click.Path(exists=True), default=None, help="Path to bitstream file (default: build/<platform>/gateware/<platform>.bit)")
def load(platform, output_dir, bitstream):
    """Load bitstream to FPGA SRAM (volatile, lost on power cycle)."""

    config = get_platform_config(platform)
    platform_inst = config["Platform"](variant=config["variant"])

    # Get bitstream path
    if bitstream is None:
        bitstream = get_bitstream_path(platform, output_dir, mode="bit")

    if not os.path.exists(bitstream):
        raise click.ClickException(
            f"Bitstream not found: {bitstream}\n"
            f"Run 'bsa-pcie-exerciser build -p {platform}' first."
        )

    click.echo(f"Loading [bold cyan]{bitstream}[/] to [bold green]{platform}[/] SRAM...")

    prog = platform_inst.create_programmer()
    prog.load_bitstream(bitstream)

    click.echo("[bold green]Load complete![/]")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--platform", "-p", type=click.Choice(list(PLATFORMS.keys()), case_sensitive=False), default="squirrel", show_default=True, help="Target platform")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Build output directory (default: build/<platform>)")
@click.option("--bitstream", "-b", type=click.Path(exists=True), default=None, help="Path to bitstream file (default: build/<platform>/gateware/<platform>.bit)")
@click.option("--address", "-a", type=str, default="0", help="Flash address offset (default: 0)")
def flash(platform, output_dir, bitstream, address):
    """Flash bitstream to SPI flash (persistent across power cycles).

    Note: After flashing, power cycle or use PCIe hot-reset to load the new bitstream.
    """

    config = get_platform_config(platform)
    platform_inst = config["Platform"](variant=config["variant"])

    # Get bitstream path - use .bit for flash (openFPGALoader handles conversion)
    if bitstream is None:
        bitstream = get_bitstream_path(platform, output_dir, mode="bit")

    if not os.path.exists(bitstream):
        raise click.ClickException(
            f"Bitstream not found: {bitstream}\n"
            f"Run 'bsa-pcie-exerciser build -p {platform}' first."
        )

    # Parse address
    try:
        flash_address = int(address, 0)  # Accepts hex (0x...) or decimal
    except ValueError:
        raise click.ClickException(f"Invalid address: {address}")

    click.echo(f"Flashing [bold cyan]{bitstream}[/] to [bold green]{platform}[/] SPI flash at 0x{flash_address:08X}...")
    click.echo("[bold yellow]Warning:[/] Power cycle required after flashing to load new bitstream.")

    prog = platform_inst.create_programmer()
    prog.flash(flash_address, bitstream)

    click.echo("[bold green]Flash complete![/] Power cycle the board to boot new bitstream.")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--platform", "-p", type=click.Choice(list(PLATFORMS.keys()), case_sensitive=False), default="squirrel", show_default=True, help="Target platform")
def detect(platform):
    """Detect FPGA via JTAG (verify cable connection)."""

    config = get_platform_config(platform)
    platform_inst = config["Platform"](variant=config["variant"])

    click.echo(f"Detecting FPGA for [bold green]{platform}[/]...")

    prog = platform_inst.create_programmer()

    # OpenFPGALoader detect is done via command line
    import subprocess
    cable = "ft2232" if platform == "squirrel" else "ft4232"
    result = subprocess.run(
        ["openFPGALoader", "-c", cable, "--detect"],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        click.echo(result.stdout)
        click.echo("[bold green]FPGA detected![/]")
    else:
        click.echo(result.stderr, err=True)
        raise click.ClickException("Failed to detect FPGA. Check cable connection.")


def main():
    cli()


if __name__ == "__main__":
    main()
