def insert_label(conn, name):
    conn.execute("INSERT INTO labels(name) VALUES (?)", (name,))
