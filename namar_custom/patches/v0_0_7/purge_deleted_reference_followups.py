from __future__ import annotations

from namar_custom.mentions.reference_cleanup import purge_deleted_reference_followups


def execute() -> None:
    """Remove existing inbox remnants of physically deleted source documents."""
    purge_deleted_reference_followups()
