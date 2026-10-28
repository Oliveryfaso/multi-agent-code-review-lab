def odd_value(values):
    result = 0
    for value in values:
        result ^= value
    return result
