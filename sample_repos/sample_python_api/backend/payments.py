from __future__ import annotations

from backend.notifications import send_payment_receipt
from backend.orders import mark_order_paid


def validate_payment_payload(payload: dict) -> bool:
    return bool(payload.get("order_id") and payload.get("amount", 0) > 0)


def charge_card(card_token: str, amount: int) -> dict:
    if not card_token:
        return {"ok": False, "error": "missing card token"}
    return {"ok": True, "provider_id": "pay_123", "amount": amount}


def process_payment(payload: dict) -> dict:
    if not validate_payment_payload(payload):
        return {"status": 400, "error": "invalid payment payload"}
    charge = charge_card(payload.get("card_token", ""), payload["amount"])
    if not charge["ok"]:
        return {"status": 402, "error": charge["error"]}
    order = mark_order_paid(payload["order_id"])
    if not order:
        return {"status": 404, "error": "order not found"}
    send_payment_receipt(order["user_id"], order["id"], charge["provider_id"])
    return {"status": 200, "payment_id": charge["provider_id"]}

