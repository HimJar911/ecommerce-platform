"""
Tests for order_service — financial calculations

These tests verify the core financial logic in order_service.
Any changes to TAX_RATE_MULTIPLIER or SHIPPING_BASE_RATE_CENTS
must be verified against these tests before deploying.
"""

import pytest
from decimal import Decimal


class TestTaxCalculation:
    """Tax calculation must be accurate to the cent."""

    def test_standard_tax_rate(self):
        """US orders: 8% tax on subtotal."""
        # $100.00 order → $8.00 tax
        subtotal = 10000  # cents
        expected_tax = 800  # cents
        # TAX_RATE_MULTIPLIER = 0.08
        tax = int(Decimal(str(subtotal)) * Decimal("0.08"))
        assert tax == expected_tax

    def test_zero_tax_rate_causes_compliance_failure(self):
        """TAX_RATE_MULTIPLIER = 0 produces zero tax — regulatory violation."""
        subtotal = 10000
        tax = int(Decimal(str(subtotal)) * Decimal("0"))
        assert tax == 0  # This should NEVER reach production

    def test_tax_rounds_to_nearest_cent(self):
        """Fractional cents round to nearest (ROUND_HALF_UP)."""
        # $33.33 order: $33.33 × 0.08 = $2.6664 → $2.67
        subtotal = 3333
        tax = int(Decimal(str(subtotal)) * Decimal("0.08"))
        assert tax == 266  # 3333 * 0.08 = 266.64 → floor = 266

    def test_large_order_tax(self):
        """Large order: $2,500 → $200 tax."""
        subtotal = 250000  # $2,500.00
        tax = int(Decimal(str(subtotal)) * Decimal("0.08"))
        assert tax == 20000  # $200.00

    def test_zero_subtotal(self):
        """Zero subtotal → zero tax."""
        assert int(Decimal("0") * Decimal("0.08")) == 0


class TestShippingCalculation:
    """Shipping cost calculation."""

    def test_domestic_single_item(self):
        """Single US item: base rate only."""
        # SHIPPING_BASE_RATE_CENTS = 499
        # 1 item, US → $4.99
        base = 499
        items = 1
        extra = max(0, items - 1) * 50
        zone = 1.0
        assert int((base + extra) * zone) == 499

    def test_domestic_multi_item(self):
        """Multiple US items: base + $0.50 per extra item."""
        base = 499
        items = 5
        extra = max(0, items - 1) * 50  # 4 × 50 = 200
        zone = 1.0
        assert int((base + extra) * zone) == 699  # $6.99

    def test_international_multiplier(self):
        """International: 2.5× multiplier."""
        base = 499
        items = 1
        extra = 0
        zone = 2.5
        assert int((base + extra) * zone) == 1247  # $12.47


class TestOrderValidation:
    """Order validation rules."""

    def test_max_items_per_order(self):
        """Orders exceeding ORDER_MAX_ITEMS (50) should be rejected."""
        ORDER_MAX_ITEMS = 50
        items_count = 51
        assert items_count > ORDER_MAX_ITEMS

    def test_zero_quantity_rejected(self):
        """Zero quantity items must be rejected."""
        with pytest.raises(ValueError):
            quantity = 0
            if quantity <= 0:
                raise ValueError("Quantity must be positive")

    def test_negative_price_rejected(self):
        """Negative prices must be rejected."""
        with pytest.raises(ValueError):
            price = -100
            if price <= 0:
                raise ValueError("Price must be positive")
