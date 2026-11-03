from dataclasses import dataclass

@dataclass(frozen=True)
class Item:
    price_cents: int
    quantity: int
