"""Unit tests for tool functions.

All tests are synchronous — no Ollama or async required.
Tools perform pure data lookups from JSON files.
"""
import datetime

import pytest

from app.tools.orders import get_order, format_order_context
from app.tools.products import search_products, format_product_context
from app.tools.returns import check_return_eligibility


# ---------------------------------------------------------------------------
# Order tool tests
# ---------------------------------------------------------------------------

class TestOrderTools:
    def test_get_order_existing(self):
        """Fetching a known order ID returns a dict with a status field."""
        order = get_order("ORD-10001")
        assert order is not None
        assert "status" in order
        assert order["order_id"] == "ORD-10001"

    def test_get_order_not_found(self):
        """Fetching a non-existent order ID returns None."""
        order = get_order("ORD-99999")
        assert order is None

    def test_get_order_normalization(self):
        """A bare numeric ID is normalized to ORD-NNNNN and finds the order."""
        # "10001" should be normalized to "ORD-10001"
        order = get_order("10001")
        assert order is not None
        assert order["order_id"] == "ORD-10001"

    def test_format_order_context_none(self):
        """format_order_context with None input returns 'Order not found'."""
        result = format_order_context(None)
        assert "Order not found" in result

    def test_format_order_context_valid(self):
        """format_order_context with a valid order dict returns a non-empty string."""
        order = {
            "order_id": "ORD-10001",
            "status": "shipped",
            "items": [{"name": "Widget", "qty": 1}],
            "total_usd": 9.99,
            "placed_date": "2026-06-01",
            "est_delivery": "2026-06-08",
            "tracking_number": "TRACK123",
        }
        result = format_order_context(order)
        assert "ORD-10001" in result
        assert "shipped" in result
        assert "TRACK123" in result


# ---------------------------------------------------------------------------
# Product tool tests
# ---------------------------------------------------------------------------

class TestProductTools:
    def test_search_products_by_name(self):
        """Searching 'headphones' returns at least one matching product."""
        results = search_products("headphones")
        assert len(results) >= 1
        names = [p["name"].lower() for p in results]
        assert any("headphones" in n for n in names)

    def test_search_products_no_results(self):
        """Searching for a nonsense term returns an empty list."""
        results = search_products("xyznonexistent12345")
        assert results == []

    def test_format_product_context_empty(self):
        """format_product_context with empty list returns 'No matching products'."""
        result = format_product_context([])
        assert "No matching products" in result

    def test_format_product_context_valid(self):
        """format_product_context with products includes name and price."""
        products = [
            {
                "product_id": "P001",
                "name": "Wireless Headphones Pro",
                "price_usd": 79.99,
                "in_stock": True,
                "description": "Great headphones.",
            }
        ]
        result = format_product_context(products)
        assert "Wireless Headphones Pro" in result
        assert "79.99" in result
        assert "In stock" in result


# ---------------------------------------------------------------------------
# Returns tool tests
# ---------------------------------------------------------------------------

class TestReturnTools:
    def _make_order(self, days_ago: int, status: str = "delivered") -> dict:
        placed = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
        return {
            "order_id": "ORD-TEST",
            "status": status,
            "placed_date": placed,
            "items": [],
            "total_usd": 0.0,
        }

    def test_return_eligibility_recent_order(self):
        """An order placed 5 days ago is within the 30-day return window."""
        order = self._make_order(days_ago=5)
        result = check_return_eligibility(order)
        assert result["eligible"] is True
        assert result["days_since_order"] == 5

    def test_return_eligibility_old_order(self):
        """An order placed 45 days ago is outside the 30-day return window."""
        order = self._make_order(days_ago=45)
        result = check_return_eligibility(order)
        assert result["eligible"] is False
        assert result["days_since_order"] == 45

    def test_return_eligibility_cancelled_order(self):
        """A cancelled order is not eligible for return regardless of age."""
        order = self._make_order(days_ago=3, status="cancelled")
        result = check_return_eligibility(order)
        assert result["eligible"] is False

    def test_return_eligibility_returned_order(self):
        """An already-returned order is not eligible again."""
        order = self._make_order(days_ago=5, status="returned")
        result = check_return_eligibility(order)
        assert result["eligible"] is False

    def test_return_eligibility_none_order(self):
        """check_return_eligibility with None returns eligible=False."""
        result = check_return_eligibility(None)
        assert result["eligible"] is False
        assert "not found" in result["reason"].lower()
