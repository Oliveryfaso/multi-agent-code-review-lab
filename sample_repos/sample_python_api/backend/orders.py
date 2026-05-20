from __future__ import annotations

INVENTORY = {"book": 3, "pen": 10}
ORDERS: list[dict] = []


def check_inventory(item_id: str, quantity: int) -> bool:
    return INVENTORY.get(item_id, 0) >= quantity


def reserve_inventory(item_id: str, quantity: int) -> None:
    if not check_inventory(item_id, quantity):
        raise ValueError("insufficient inventory")
    INVENTORY[item_id] -= quantity


def calculate_total(item_id: str, quantity: int) -> int:
    prices = {"book": 30, "pen": 5}
    return prices.get(item_id, 0) * quantity


def create_order(user_id: str, item_id: str, quantity: int) -> dict:
    reserve_inventory(item_id, quantity)
    order = {
        "id": f"order-{len(ORDERS) + 1}",
        "user_id": user_id,
        "item_id": item_id,
        "quantity": quantity,
        "total": calculate_total(item_id, quantity),
        "status": "created",
    }
    ORDERS.append(order)
    return order


def mark_order_paid(order_id: str) -> dict | None:
    for order in ORDERS:
        if order["id"] == order_id:
            order["status"] = "paid"
            return order
    return None

