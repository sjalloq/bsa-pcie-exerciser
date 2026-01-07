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

# SPDX line (within the header).
SPDX_LINE_PATTERN = re.compile(
    r"^#\s*SPDX-License-Identifier:\s+(.+)$",
    re.MULTILINE
)

# Copyright line pattern - supports single year, range, or year list.
# Examples:
#   "Copyright (c) 2025 Author"
#   "Copyright (c) 2024-2025 Author"
#   "Copyright (c) 2024, 2026 Author"
COPYRIGHT_LINE_PATTERN = re.compile(
    r"^#\s*Copyright\s+\(c\)\s+([0-9,\s-]+)\s+(.+)$",
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


def _parse_years(year_text: str) -> list[int]:
    """Parse a year/range/list string into sorted unique years."""
    years = [int(y) for y in re.findall(r"\d{4}", year_text)]
    return sorted(set(years))


def _format_years(years: list[int]) -> str:
    """Format years as single, range, or list."""
    if not years:
        return "none"
    years = sorted(set(years))
    if len(years) == 1:
        return str(years[0])
    if years[-1] - years[0] + 1 == len(years):
        return f"{years[0]}-{years[-1]}"
    return ", ".join(str(y) for y in years)


def check_copyright(path: Path) -> tuple[bool, str, list[int] | None]:
    """
    Check if file has correct copyright header.

    Returns:
        (is_valid, message, [years] or None)
    """
    if not path.exists():
        return True, "File not found (probably deleted)", None

    if should_skip(path):
        return True, "Skipped", None

    try:
        content = path.read_text()
    except Exception as e:
        return False, f"Cannot read: {e}", None

    header = content[:500]

    copyright_lines = list(COPYRIGHT_LINE_PATTERN.finditer(header))
    if not copyright_lines:
        return False, "No copyright header found", None

    spdx_match = SPDX_LINE_PATTERN.search(header)
    if not spdx_match:
        return False, "Missing SPDX-License-Identifier line", None

    license_id = spdx_match.group(1).strip()
    if license_id != LICENSE:
        return False, f"license should be {LICENSE}", None

    author_years = None
    for match in copyright_lines:
        year_text = match.group(1).strip()
        author = match.group(2).strip()
        if author == AUTHOR:
            author_years = _parse_years(year_text)
            break

    if author_years is None:
        return True, "OK", None

    if CURRENT_YEAR not in author_years:
        expected = _format_years(author_years + [CURRENT_YEAR])
        return False, f"year list should include {CURRENT_YEAR} (expected {expected})", author_years

    return True, "OK", author_years


def update_copyright_year(path: Path, year_info: list[int]) -> bool:
    """Update the current author's line to include the current year."""

    try:
        content = path.read_text()
        updated = False

        def _replace(match: re.Match) -> str:
            nonlocal updated
            year_text = match.group(1).strip()
            author = match.group(2).strip()
            if author != AUTHOR:
                return match.group(0)
            years = _parse_years(year_text)
            if CURRENT_YEAR in years:
                return match.group(0)
            years.append(CURRENT_YEAR)
            years = sorted(set(years))
            updated = True
            year_text = _format_years(years)
            return f"# Copyright (c) {year_text} {author}"

        new_content = COPYRIGHT_LINE_PATTERN.sub(_replace, content)

        if updated and new_content != content:
            path.write_text(new_content)
            return True
        return False
    except Exception as e:
        print(f"  Error updating {path}: {e}", file=sys.stderr)
        return False


def format_year_info(year_info: list[int] | None) -> str:
    """Format year info for display."""
    if year_info is None:
        return "none"
    return _format_years(year_info)


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
    auto_fix = args.fix or pre_commit_mode
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
            if auto_fix and year_info is not None:
                if CURRENT_YEAR not in year_info:
                    if update_copyright_year(path, year_info):
                        old_fmt = format_year_info(year_info)
                        new_fmt = _format_years(sorted(set(year_info + [CURRENT_YEAR])))
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
            print(f"\nExpected: Copyright (c) <years> {AUTHOR} (include {CURRENT_YEAR})")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
