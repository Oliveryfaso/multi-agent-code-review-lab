def emit(path):
    with open(path, "w", encoding="ascii", newline=None) as stream:
        stream.write("oak\n")
