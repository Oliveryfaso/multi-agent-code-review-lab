def read_value(data, log):
    try:
        return data["value"]
    finally:
        log.append("closed")
