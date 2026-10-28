from db_ops import insert_label


def add_pair(conn):
    insert_label(conn, "Alder")
    insert_label(conn, "alder")
