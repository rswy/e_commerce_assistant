"""Product catalog tool — reads from mock_products.json."""
import json
from pathlib import Path
from typing import Optional

_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "mock_products.json"
_products: dict = {}

def _load():
    global _products
    if not _products:
        with open(_DATA_FILE) as f:
            data = json.load(f)
        _products = {p["product_id"]: p for p in data}

def get_product(product_id: str) -> Optional[dict]:
    _load()
    return _products.get(product_id.upper())

def search_products(query: str, limit: int = 3) -> list[dict]:
    """Simple keyword search over product name + description."""
    _load()
    query_lower = query.lower()
    results = []
    for p in _products.values():
        if query_lower in p["name"].lower() or query_lower in p.get("description", "").lower():
            results.append(p)
        if len(results) >= limit:
            break
    return results

def format_product_context(products: list[dict]) -> str:
    if not products:
        return "No matching products found."
    lines = []
    for p in products:
        stock = "In stock" if p.get("in_stock") else "Out of stock"
        lines.append(f"{p['name']} (ID: {p['product_id']}): ${p['price_usd']:.2f} — {stock}. {p.get('description','')[:100]}")
    return "\n".join(lines)
