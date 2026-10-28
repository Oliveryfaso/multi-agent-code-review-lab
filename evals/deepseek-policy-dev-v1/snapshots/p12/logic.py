def remove_zeros(values):
    pending = list(values)
    for index, value in enumerate(pending):
        if value == 0:
            del pending[index]
    return pending
