from builder import invoice_total
from records import Item

def quote_order(price_cents, quantity, discount_percent, delivery_cents):
    return invoice_total([Item(price_cents, quantity)], discount_percent, delivery_cents)
