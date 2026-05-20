from __future__ import annotations

OUTBOX: list[dict] = []


def send_email(to_user: str, subject: str, body: str) -> None:
    OUTBOX.append({"to": to_user, "subject": subject, "body": body})


def send_payment_receipt(user_id: str, order_id: str, payment_id: str) -> None:
    send_email(
        user_id,
        "Payment receipt",
        f"Order {order_id} was paid with payment {payment_id}.",
    )


def send_low_stock_alert(item_id: str) -> None:
    send_email("ops", "Low stock alert", f"Inventory is low for {item_id}.")

