import unittest
from app import quote_order

class InvoiceChecks(unittest.TestCase):
    def test_discount_excludes_delivery(self):
        self.assertEqual(quote_order(1000, 2, 25, 500), 2000)
    def test_no_discount(self):
        self.assertEqual(quote_order(1000, 2, 0, 500), 2500)
    def test_no_delivery(self):
        self.assertEqual(quote_order(1000, 2, 25, 0), 1500)
    def test_quantity_and_delivery(self):
        self.assertEqual(quote_order(500, 3, 20, 300), 1500)
    def test_full_discount_keeps_delivery(self):
        self.assertEqual(quote_order(1000, 2, 100, 500), 500)
