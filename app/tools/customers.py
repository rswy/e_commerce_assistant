"""Customer profile lookup tool."""
import json
from pathlib import Path
from typing import Optional

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "mock_customers.json"
_customers: dict = {}

def _load():
    global _customers
    if not _customers:
        with open(_DATA_FILE) as f:
            data = json.load(f)
        _customers = {c["customer_id"]: c for c in data}

def get_customer(customer_id: str) -> Optional[dict]:
    _load()
    return _customers.get(customer_id.upper())

def get_customer_by_email(email: str) -> Optional[dict]:
    _load()
    for c in _customers.values():
        if c.get("email", "").lower() == email.lower():
            return c
    return None
