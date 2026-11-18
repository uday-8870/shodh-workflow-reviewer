"""Independent routing oracle for non-circular verification of synthetic bundle labels."""

from __future__ import annotations

from typing import Any, Dict, Optional


def independent_ticket_oracle(ticket: Dict[str, Any], wait_threshold_minutes: float = 30.0) -> str:
    """Independent reference implementation of the Shodh ticket routing policy."""
    cat = ticket.get("category")
    if cat is None or (isinstance(cat, str) and cat.strip() == ""):
        return "manual_review"

    if ticket.get("priority") == "urgent" and ticket.get("tier") != "sandbox":
        return "priority_queue"

    wait_min: Optional[float] = None
    if "waiting_minutes" in ticket and ticket["waiting_minutes"] is not None:
        wait_min = float(ticket["waiting_minutes"])
    elif "waiting_seconds" in ticket and ticket["waiting_seconds"] is not None:
        wait_min = float(ticket["waiting_seconds"]) / 60.0
    elif "waiting_hours" in ticket and ticket["waiting_hours"] is not None:
        wait_min = float(ticket["waiting_hours"]) * 60.0

    if wait_min is not None and wait_min >= wait_threshold_minutes:
        return "escalation"

    return "normal_queue"
