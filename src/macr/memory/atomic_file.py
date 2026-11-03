"""Same-directory replacement; failed temporary bytes remain for diagnosis."""
import os
from pathlib import Path
from uuid import uuid4


def atomic_write(path: Path, data: bytes, *, dir_fd: int | None = None, before_replace=None) -> None:
    """Legacy path calls are unchanged; new callers may pin a parent descriptor."""
    path = Path(path)
    if dir_fd is not None and (path.name != str(path) or path.name in ('', '.', '..')):
        raise ValueError('atomic leaf required')
    temp = path.with_name(f'{path.name}-{uuid4()}.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if before_replace is not None: before_replace()
    if dir_fd is None:
        os.replace(temp, path)
    else:
        os.replace(temp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
