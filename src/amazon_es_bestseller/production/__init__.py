"""Production promotion artifacts for the offline Amazon.es workflow."""

from .spanish_master import build_spanish_master, verify_artifact_hash

__all__ = ["build_spanish_master", "verify_artifact_hash"]
