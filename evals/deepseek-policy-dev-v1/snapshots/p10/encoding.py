def encode_payload(text):
    payload = text.encode("utf-8")
    return payload, len(text)
