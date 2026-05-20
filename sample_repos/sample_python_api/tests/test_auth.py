import unittest

from backend.orders import INVENTORY, create_order
from backend.payments import process_payment
from backend.server import dashboard_handler, login_handler


class AuthTests(unittest.TestCase):
    def test_login_success(self):
        response = login_handler({"username": "learner", "password": "123456"})
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["token"], "learner-token")

    def test_login_failure(self):
        response = login_handler({"username": "learner", "password": "wrong"})
        self.assertEqual(response["status"], 401)

    def test_dashboard_requires_auth(self):
        response = dashboard_handler({})
        self.assertEqual(response["status"], 401)

class OrderPaymentTests(unittest.TestCase):
    def setUp(self):
        INVENTORY["book"] = 3

    def test_create_order_reserves_inventory(self):
        order = create_order("learner", "book", 1)
        self.assertEqual(order["total"], 30)
        self.assertEqual(INVENTORY["book"], 2)

    def test_process_payment_rejects_invalid_payload(self):
        response = process_payment({"order_id": "", "amount": 0})
        self.assertEqual(response["status"], 400)


if __name__ == "__main__":
    unittest.main()
