#!/usr/bin/env python3
"""Add copyright headers to all Python files in archi3D."""

import os
from pathlib import Path

COPYRIGHT_HEADER = """# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved
"""

def add_copyright_header(file_path: Path) -> bool:
    """Add copyright header to a Python file if not already present."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Skip if already has copyright
    if 'Copyright' in content[:500]:
        print(f"SKIP: {file_path} (already has copyright)")
        return False

    # Add copyright header at the top
    new_content = COPYRIGHT_HEADER + '\n' + content

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"ADDED: {file_path}")
    return True

def main():
    """Add copyright headers to all .py files in src/archi3d/."""
    src_dir = Path('src/archi3d')

    if not src_dir.exists():
        print(f"ERROR: Directory not found: {src_dir}")
        return

    count = 0
    for py_file in src_dir.rglob('*.py'):
        if add_copyright_header(py_file):
            count += 1

    print(f"\nAdded copyright headers to {count} files.")

if __name__ == '__main__':
    main()
