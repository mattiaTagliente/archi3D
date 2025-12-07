# Copyright (C) 2025 Francesca Falcone and Mattia Tagliente
# All Rights Reserved

# src/archi3d/utils/uploads.py
from __future__ import annotations
from pathlib import Path
import fal_client
from archi3d.utils.text import slugify

def upload_file_safely(path: Path) -> str:
    """
    Uploads a file using fal_client, safely handling Unicode characters in the path.

    It temporarily renames the file to a purely ASCII-safe name, uploads it,
    and then reliably renames it back to its original name. This avoids both
    in-memory loading of large files and Unicode errors in the client library.

    Args:
        path: The absolute Path object for the file to upload.

    Returns:
        The URL of the uploaded file.
    """
    if not path.exists():
        raise FileNotFoundError(f"Cannot upload non-existent file: {path}")

    # If the path is already ascii, no need for the rename dance
    try:
        path.as_posix().encode('ascii')
        is_safe = True
    except UnicodeEncodeError:
        is_safe = False

    if is_safe:
        return fal_client.upload_file(path)

    # The path contains non-ASCII characters, so we must rename it.
    # Create a temporary, safe (ASCII) filename in the same directory.
    temp_name = slugify(path.stem) + path.suffix
    temp_path = path.with_name(temp_name)

    try:
        # Rename the original file to the temporary name.
        # This is an atomic and fast metadata-only operation.
        path.rename(temp_path)

        # Upload using the safe, temporary path.
        url = fal_client.upload_file(temp_path)

        return url

    finally:
        # CRITICAL: This block ensures the file is always renamed back,
        # even if the upload fails or is cancelled.
        if temp_path.exists():
            temp_path.rename(path)