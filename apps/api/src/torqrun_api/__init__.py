"""Torqrun control-plane API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("torqrun-api")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0+unknown"
