"""Test-support that ships with the app, because AN-7's scanner is a gate."""
from .scanner import (
    PATTERNS, Finding, assert_clean, scan_object, scan_paths, scan_text,
)

__all__ = ["scan_text", "scan_object", "scan_paths", "assert_clean",
           "Finding", "PATTERNS"]
