"""Pluggable lead sources.

Each source is enabled from config/prospecting.toml, fails soft, and writes
through the same lead store as OpenStreetMap.
"""

from .runner import run_configured_sources

__all__ = ["run_configured_sources"]
