def split_field(text):
    name, value = text.split("=", 1)
    return name, value
