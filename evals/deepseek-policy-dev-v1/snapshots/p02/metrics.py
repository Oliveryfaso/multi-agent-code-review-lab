def summarize(values):
    source = iter(values)
    count = sum(1 for _ in source)
    total = sum(source)
    return count, total
