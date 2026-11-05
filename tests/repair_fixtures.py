"""Public development source strings; never imported or executed as targets."""
SOURCES = {
    'app.py': 'from builder import invoice_total\nfrom records import Item\n\ndef quote_order(price_cents, quantity, discount_percent, delivery_cents):\n    return invoice_total([Item(price_cents, quantity)], discount_percent, delivery_cents)\n',
    'builder.py': 'def invoice_total(items, discount_percent, delivery_cents):\n    subtotal = sum(item.price_cents * item.quantity for item in items)\n    return (subtotal + delivery_cents) * (100 - discount_percent) // 100\n',
    'records.py': 'from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Item:\n    price_cents: int\n    quantity: int\n',
    'checks.py': 'import unittest\nfrom app import quote_order\n\nclass InvoiceChecks(unittest.TestCase):\n    def test_discount_excludes_delivery(self):\n        self.assertEqual(quote_order(1000, 2, 25, 500), 2000)\n    def test_no_discount(self):\n        self.assertEqual(quote_order(1000, 2, 0, 500), 2500)\n    def test_no_delivery(self):\n        self.assertEqual(quote_order(1000, 2, 25, 0), 1500)\n    def test_quantity_and_delivery(self):\n        self.assertEqual(quote_order(500, 3, 20, 300), 1500)\n    def test_full_discount_keeps_delivery(self):\n        self.assertEqual(quote_order(1000, 2, 100, 500), 500)\n',
}

OLD = 'return (subtotal + delivery_cents) * (100 - discount_percent) // 100'

NEW = 'return subtotal * (100 - discount_percent) // 100 + delivery_cents'
