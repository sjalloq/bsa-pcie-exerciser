#!/usr/bin/env python3
#
# Copyright Header Checker
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Pre-commit hook to verify Python files have correct copyright headers.
# Checks that edited files contain the required copyright format with current year.
#
# Usage:
#   As pre-commit hook: ln -s ../../hooks/check_copyright.py .git/hooks/pre-commit
#   Manual check:       python hooks/check_copyright.py [--fix] [files...]
#   Check all:          python hooks/check_copyright.py --all
#

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CURRENT_YEAR = datetime.now().year
AUTHOR = "Shareef Jalloq"
LICENSE = "BSD-2-Clause"

# Required copyright header pattern - supports single year or range
# Matches: "Copyright (c) 2025 Author" or "Copyright (c) 2024-2025 Author"
COPYRIGHT_PATTERN = re.compile(
    r"^#\s*\n"
    r"^#\s*.+\n"  # Title line
    r"^#\s*\n"
    r"^#\s*Copyright\s+\(c\)\s+(\d{4})(?:-(\d{4}))?\s+(.+)\n"
    r"^#\s*SPDX-License-Identifier:\s+(.+)\n",
    re.MULTILINE
)

# Looser pattern to find any copyright line - supports single year or range
COPYRIGHT_LINE_PATTERN = re.compile(
    r"^#\s*Copyright\s+\(c\)\s+(\d{4})(?:-(\d{4}))?\s+(.+)$",
    re.MULTILINE
)

# Files/directories to skip
SKIP_PATTERNS = [
    "__pycache__",
    ".git",
    ".venv",
    "build",
    "external",  # Don't check submodules
    "__init__.py",  # Often minimal
]


def should_skip(path: Path) -> bool:
    """Check if file should be skipped."""
    path_str = str(path)
    for pattern in SKIP_PATTERNS:
        if pattern in path_str:
            return True
    return False


def get_staged_files() -> list[Path]:
    """Get list of staged Python files (for pre-commit hook mode)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in result.stdout.strip().split("\n"):
            if line and line.endswith(".py"):
                files.append(Path(line))
        return files
    except subprocess.CalledProcessError:
        return []


def get_all_python_files(root: Path) -> list[Path]:
    """Get all Python files in the project."""
    files = []
    for py_file in root.rglob("*.py"):
        if not should_skip(py_file):
            files.append(py_file)
    return files


def check_copyright(path: Path) -> tuple[bool, str, tuple[int, int | None] | None]:
    """
    Check if file has correct copyright header.

    Returns:
        (is_valid, message, (start_year, end_year or None))
    """
    if not path.exists():
        return True, "File not found (probably deleted)", None

    if should_skip(path):
        return True, "Skipped", None

    try:
        content = path.read_text()
    except Exception as e:
        return False, f"Cannot read: {e}", None

    # Check for full header pattern
    match = COPYRIGHT_PATTERN.search(content[:500])  # Only check first 500 chars
    if match:
        start_year = int(match.group(1))
        end_year = int(match.group(2)) if match.group(2) else None
        author = match.group(3).strip()
        license_id = match.group(4).strip()

        # The effective year is the end year (if range) or the single year
        effective_year = end_year if end_year else start_year

        issues = []
        if effective_year != CURRENT_YEAR:
            if end_year:
                issues.append(f"year range {start_year}-{end_year} should end with {CURRENT_YEAR}")
            else:
                issues.append(f"year {start_year} should be {start_year}-{CURRENT_YEAR}")
        if author != AUTHOR:
            issues.append(f"author mismatch")
        if license_id != LICENSE:
            issues.append(f"license should be {LICENSE}")

        if issues:
            return False, ", ".join(issues), (start_year, end_year)
        return True, "OK", (start_year, end_year)

    # Check for any copyright line (partial header)
    line_match = COPYRIGHT_LINE_PATTERN.search(content[:500])
    if line_match:
        start_year = int(line_match.group(1))
        end_year = int(line_match.group(2)) if line_match.group(2) else None
        effective_year = end_year if end_year else start_year

        if effective_year != CURRENT_YEAR:
            if end_year:
                return False, f"Found copyright but year range {start_year}-{end_year} should end with {CURRENT_YEAR}", (start_year, end_year)
            else:
                return False, f"Found copyright but year {start_year} should be {start_year}-{CURRENT_YEAR}", (start_year, end_year)
        return False, "Copyright found but header format incorrect", (start_year, end_year)

    return False, "No copyright header found", None


def update_copyright_year(path: Path, year_info: tuple[int, int | None]) -> bool:
    """Update copyright year in file to show range ending with current year."""
    start_year, end_year = year_info

    # If already current year, nothing to do
    effective_year = end_year if end_year else start_year
    if effective_year == CURRENT_YEAR:
        return False

    try:
        content = path.read_text()

        if end_year:
            # Has a range already, update end year: "2024-2025" -> "2024-2026"
            new_content = re.sub(
                rf"(Copyright\s+\(c\)\s+{start_year})-{end_year}(\s+)",
                rf"\g<1>-{CURRENT_YEAR}\g<2>",
                content
            )
        else:
            # Single year, convert to range: "2025" -> "2025-2026"
            new_content = re.sub(
                rf"(Copyright\s+\(c\)\s+){start_year}(\s+)",
                rf"\g<1>{start_year}-{CURRENT_YEAR}\g<2>",
                content
            )

        if new_content != content:
            path.write_text(new_content)
            return True
        return False
    except Exception as e:
        print(f"  Error updating {path}: {e}", file=sys.stderr)
        return False


def format_year_info(year_info: tuple[int, int | None] | None) -> str:
    """Format year info for display."""
    if year_info is None:
        return "none"
    start_year, end_year = year_info
    if end_year:
        return f"{start_year}-{end_year}"
    return str(start_year)


def main():
    parser = argparse.ArgumentParser(
        description="Check/fix copyright headers in Python files"
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Files to check (default: staged files)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all Python files in project",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Update copyright year in files with wrong year",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only show errors",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all files including OK ones",
    )
    args = parser.parse_args()

    # Determine which files to check
    # When files are passed as args (pre-commit mode), be quiet by default
    pre_commit_mode = bool(args.files)
    if args.files:
        files = args.files
    elif args.all:
        root = Path(__file__).parent.parent
        files = get_all_python_files(root)
    else:
        # Pre-commit hook mode: check staged files
        files = get_staged_files()
        if not files:
            sys.exit(0)  # No Python files staged

    # Check files
    errors = []
    fixed = []

    for path in sorted(files):
        is_valid, message, year_info = check_copyright(path)

        if not is_valid:
            if args.fix and year_info is not None:
                start_year, end_year = year_info
                effective_year = end_year if end_year else start_year
                if effective_year != CURRENT_YEAR:
                    if update_copyright_year(path, year_info):
                        old_fmt = format_year_info(year_info)
                        new_fmt = f"{start_year}-{CURRENT_YEAR}"
                        fixed.append(path)
                        print(f"Fixed: {path} ({old_fmt} -> {new_fmt})")
                        continue

            errors.append((path, message))
            print(f"{path}: {message}")
        elif args.verbose or (not args.quiet and not pre_commit_mode):
            print(f"OK: {path}")

    # Summary (only in non-pre-commit mode or if there are errors)
    if fixed and not pre_commit_mode:
        print(f"\nFixed {len(fixed)} file(s)")

    if errors:
        if not pre_commit_mode:
            print(f"\n{len(errors)} file(s) with copyright issues")
            print(f"\nExpected: Copyright (c) <start>-{CURRENT_YEAR} {AUTHOR}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
