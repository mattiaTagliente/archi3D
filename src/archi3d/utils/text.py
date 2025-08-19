# src/archi3d/utils/text.py
from __future__ import annotations
import re, hashlib, unicodedata

_slug_re = re.compile(r"[^a-z0-9._-]+")
def slugify(text: str) -> str:
    """
    Lowercase, keep [a-z0-9-], collapse dashes.
    """
    if not isinstance(text, str):
        return ""
    # Strip accents and normalize
    s = unicodedata.normalize("NFKD", text)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    # Remove invalid chars
    s = _slug_re.sub("-", s).strip("-._")
    # Collapse dashes
    s = re.sub(r"-{2,}", "-", s)
    return s

def get_stable_hash(text: str, length: int = 8) -> str:
    """Returns a stable, fixed-length hash of a string."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=length // 2).hexdigest()