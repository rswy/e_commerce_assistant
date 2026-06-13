"""Order lookup tool — reads from mock_orders.json."""
import json
from pathlib import Path
from typing import Optional

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "mock_orders.json"
_orders: dict = {}

def _load():
    global _orders
    if not _orders:
        with open(_DATA_FILE) as f:
            data = json.load(f)
        _orders = {o["order_id"]: o for o in data}

def get_order(order_id: str) -> Optional[dict]:
    """Return order dict or None if not found."""
    _load()
    # Normalize: "12345" → "ORD-12345"
    normalized = order_id.upper()
    if not normalized.startswith("ORD-"):
        normalized = f"ORD-{normalized.lstrip('ORD-')}"
    return _orders.get(normalized)

def get_orders_for_customer(customer_id: str) -> list[dict]:
    """Return all orders for a customer."""
    _load()
    return [o for o in _orders.values() if o.get("customer_id") == customer_id]

def format_order_context(order: dict) -> str:
    """Format order data for LLM context injection."""
    if not order:
        return "Order not found in system."
    items_str = ", ".join(f"{i['name']} x{i['qty']}" for i in order.get("items", []))
    return (
        f"Order {order['order_id']}: Status={order['status']}, "
        f"Items=[{items_str}], "
        f"Total=${order['total_usd']:.2f}, "
        f"Placed={order['placed_date']}, "
        f"Est. delivery={order.get('est_delivery', 'TBD')}, "
        f"Tracking={order.get('tracking_number', 'Not yet assigned')}."
    )
