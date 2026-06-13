"""Returns and refund processing tool."""
import json
from pathlib import Path
from typing import Optional
import datetime

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "mock_returns.json"
_returns: dict = {}

# Return window in days
RETURN_WINDOW_DAYS = 30

def _load():
    global _returns
    if not _returns:
        try:
            with open(_DATA_FILE) as f:
                data = json.load(f)
            _returns = {r["return_id"]: r for r in data}
        except FileNotFoundError:
            _returns = {}

def get_return(return_id: str) -> Optional[dict]:
    _load()
    return _returns.get(return_id.upper())

def check_return_eligibility(order: dict) -> dict:
    """Check if an order is within the return window."""
    if not order:
        return {"eligible": False, "reason": "Order not found"}
    placed = datetime.date.fromisoformat(order["placed_date"])
    days_since = (datetime.date.today() - placed).days
    eligible = days_since <= RETURN_WINDOW_DAYS and order["status"] not in ("returned", "cancelled")
    reason = (
        f"Order is {days_since} days old; return window is {RETURN_WINDOW_DAYS} days."
        if not eligible else
        f"Order is eligible for return ({days_since} days old, {RETURN_WINDOW_DAYS - days_since} days remaining)."
    )
    return {"eligible": eligible, "reason": reason, "days_since_order": days_since}

def format_return_context(eligibility: dict) -> str:
    status = "ELIGIBLE" if eligibility["eligible"] else "NOT ELIGIBLE"
    return f"Return status: {status}. {eligibility['reason']}"
