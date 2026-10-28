from encoding import encode_payload


def frame(text):
    payload, size = encode_payload(text)
    return str(size).encode("ascii") + b":" + payload
