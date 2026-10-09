"""Same-directory replacement; failed temporary bytes remain for diagnosis."""
import os
from pathlib import Path
from uuid import uuid4


def atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_name(f'{path.name}-{uuid4()}.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
