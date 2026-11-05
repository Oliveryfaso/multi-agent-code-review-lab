def invoice_total(items, discount_percent, delivery_cents):
    subtotal = sum(item.price_cents * item.quantity for item in items)
    return (subtotal + delivery_cents) * (100 - discount_percent) // 100
