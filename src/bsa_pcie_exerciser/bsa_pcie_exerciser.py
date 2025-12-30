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
        "platform_module": "bsa_pcie_exerciser.platform.spec_a7",
        "crg_module": "bsa_pcie_exerciser.soc.spec_a7",
        "crg_class": "SPECA7CRG",
        "variant": "xc7a35t",
        "sys_clk_freq": 125e6,
    },
    "squirrel": {
        "description": "Squirrel/CaptainDMA (XC7A35T)",
        "platform_module": "bsa_pcie_exerciser.platform.squirrel",
        "crg_module": "bsa_pcie_exerciser.soc.squirrel",
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


# =============================================================================
# CLI
# =============================================================================

# Configure rich_click styling
# click.rich_click.USE_MARKDOWN = True
# click.rich_click.USE_RICH_MARKUP = False

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


@click.group(context_settings=CONTEXT_SETTINGS)
def cli():
    """BSA PCIe Exerciser CLI Tool

    This tool can be used to build and flash supported boards.
    """
    pass


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option("--platform", "-p", type=click.Choice(list(PLATFORMS.keys()), case_sensitive=False), default="squirrel", show_default=True, help="Target platform")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Build output directory (default: build/<platform>)")
def build(platform, output_dir):

    # Get platform configuration
    config = get_platform_config(platform)

    # Create platform
    platform_inst = config["Platform"](variant=config["variant"])

    # Import SoC here to avoid circular imports
    from bsa_pcie_exerciser.soc import BSAExerciserSoC

    click.echo(f"Building for [bold green]{platform}[/] ({config['variant']})...")

    # Create SoC
    soc = BSAExerciserSoC(
        platform_inst,
        sys_clk_freq=int(config["sys_clk_freq"]),
        crg_cls=config["CRG"],
    )

    # Build
    build_output_dir = output_dir or f"build/{platform}"
    builder = Builder(soc, output_dir=build_output_dir)
    builder.build()

    click.echo(f"[bold green]Build complete![/] Output: {build_output_dir}")


def main():
    cli()


if __name__ == "__main__":
    main()
