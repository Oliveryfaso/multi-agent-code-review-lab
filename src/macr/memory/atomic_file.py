"""Same-directory replacement; failed temporary bytes remain for diagnosis."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
from threading import RLock
from uuid import uuid4


_NAMESPACE_LOCK = RLock()


@contextmanager
def namespace_snapshot():
    """Coordinate a same-process allocation scan with atomic namespace changes."""
    with _NAMESPACE_LOCK:
        yield


def atomic_write(path: Path, data: bytes, *, dir_fd: int | None = None, before_replace=None) -> None:
    """Optional anchored parent; legacy callers keep their existing behavior."""
    path = Path(path)
    if dir_fd is not None and (path.name != str(path) or path.name in ('', '.', '..')):
        raise ValueError('atomic leaf required')
    temp = path.with_name(f'{path.name}-{uuid4()}.tmp')
    with namespace_snapshot():
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    with namespace_snapshot():
        if before_replace is not None:
            before_replace()
        if dir_fd is None:
            os.replace(temp, path)
        else:
            os.replace(temp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)


def tree_usage(root: Path) -> dict[str, int]:
    """Strict allocation snapshot coordinated with same-process atomic writes.

    Count directories, AppleDouble and retained failed-write temporary files.
    Missing entries and symlinks fail; this lock does not coordinate processes.
    """
    root = Path(root)
    usage = {'files': 0, 'logical_bytes': 0, 'allocated_bytes': 0}

    def walk_error(error):
        raise error

    with namespace_snapshot():
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise RuntimeError('unexpected_fixture_entry')
        for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
            for name in directories:
                entry = (Path(directory) / name).lstat()
                if stat.S_ISLNK(entry.st_mode):
                    raise RuntimeError('unexpected_fixture_link')
                if not stat.S_ISDIR(entry.st_mode):
                    raise RuntimeError('unexpected_fixture_entry')
            for path in [Path(directory)] + [Path(directory) / name for name in files]:
                entry = path.lstat()
                if not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode)):
                    raise RuntimeError('unexpected_fixture_entry')
                usage['allocated_bytes'] += entry.st_blocks * 512
                if stat.S_ISREG(entry.st_mode):
                    usage['files'] += 1
                    usage['logical_bytes'] += entry.st_size
    return usage
