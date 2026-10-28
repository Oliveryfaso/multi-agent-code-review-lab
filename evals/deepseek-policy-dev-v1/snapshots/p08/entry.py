from layout import note_path


def read_note(root):
    with open(note_path(root), "r", encoding="utf-8") as stream:
        return stream.read()
