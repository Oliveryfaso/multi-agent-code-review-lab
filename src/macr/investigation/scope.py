"""One inventory/policy for all readers; exclusions are application policy, not Git."""
from __future__ import annotations

import fnmatch
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from .records import FileEntry, RunError, ScopeManifest, ScopeProfile, TaskSpec
from .validation import ContractError, validate_relative

EXCLUDED_DIRS = {".git", ".hg", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
                 ".ruff_cache", ".macr_cache", ".cache", ".tox", "artifacts", "traces", "patches", "reports",
                 ".aws", ".ssh", ".codex", ".agents", "secrets", "credentials", "oracles", "oracle"}
TEXT_SUFFIXES = {".py", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".json", ".md", ".txt"}
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*credentials*", "*secret*.json")
PRIVATE_DIRS = {'.git', '.hg', '.aws', '.ssh', '.codex', '.agents', 'secrets', 'credentials', 'oracles', 'oracle'}


def paths_overlap(left: Path, right: Path):
    """Conservative resolved-path overlap across case/Unicode-insensitive volumes."""
    from unicodedata import normalize
    def parts(path):
        return tuple(normalize('NFC',part).casefold() for part in path.parts)
    left,right=parts(left),parts(right)
    return left[:len(right)]==right or right[:len(left)]==left


def validate_workspace_path(path: Path, *, output=False):
    """Explicit artifact roots may be read, but never private/secret ancestry."""
    excluded = PRIVATE_DIRS if output else EXCLUDED_DIRS
    if any(part.casefold() in excluded or part.startswith('._') or
           any(fnmatch.fnmatchcase(part.casefold(), pattern) for pattern in SECRET_PATTERNS)
           for part in Path(path).absolute().parts):
        raise ContractError('forbidden_path', 'protected_output' if output else 'protected_source')


@contextmanager
def open_directory(path: Path, *, create=False):
    """Anchor every absolute component; do not follow even an ancestor symlink."""
    path = Path(path).absolute()
    if '..' in path.parts or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ContractError('forbidden_path', 'directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = [os.open(path.anchor, flags)]
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError: pass
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)


@contextmanager
def safe_parent(root: Path, relative: str, *, create=False):
    validate_relative(relative)
    with open_directory(root) as root_fd:
        descriptors = []; current = root_fd
        try:
            for part in relative.split('/')[:-1]:
                if create:
                    try: os.mkdir(part, 0o700, dir_fd=current)
                    except FileExistsError: pass
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(current)
            yield current, relative.split('/')[-1]
        finally:
            for descriptor in reversed(descriptors): os.close(descriptor)


def exclusion(relative: str, profile: ScopeProfile) -> str:
    parts = relative.split("/")
    if any(p.casefold() in EXCLUDED_DIRS for p in parts):
        return "excluded_subtree"
    if any(p.startswith("._") for p in parts):
        return "appledouble"
    if any(fnmatch.fnmatchcase(p.lower(), pattern) for p in parts for pattern in SECRET_PATTERNS):
        return "secret_path"
    if any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(p, pattern) for pattern in profile.exclude_patterns for p in parts):
        return "configured_exclusion"
    return ""


def walk_entries(root: Path, profile: ScopeProfile | None = None):
    """A bounded metadata inventory; excluded directories are never descended."""
    root = Path(root).absolute(); profile = profile or ScopeProfile()
    pending = [('', root)]; count = metadata_bytes = 0
    while pending:
        directory_rel, directory = pending.pop()
        try:
            entries = []
            with open_directory(directory) as directory_fd, os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    rel = '/'.join(filter(None, (directory_rel, entry.name)))
                    count += 1
                    try: metadata_bytes += len(rel.encode('utf-8')) + 128
                    except UnicodeError: raise ContractError('forbidden_path', 'filename_encoding') from None
                    if count > 20000 or metadata_bytes > 4 * 1024 * 1024:
                        raise ContractError('scope_limit', 'metadata')
                    try: info = entry.stat(follow_symlinks=False)
                    except OSError:
                        entries.append((rel, 'unreadable', 0)); continue
                    kind = 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'special'
                    entries.append((rel, kind, info.st_size))
        except OSError:
            yield directory_rel or '.', 'unreadable', 0
            continue
        for rel, kind, size in sorted(entries):
            yield rel, kind, size
            if kind == 'directory' and not exclusion(rel, profile): pending.append((rel, root / rel))


def resolve_allowed(root: Path, relative: str, scope: ScopeManifest) -> Path:
    validate_relative(relative)
    if scope.errors or relative not in {f.path for f in scope.eligible}:
        raise ContractError("forbidden_path", "scope")
    root = Path(root).absolute()
    with safe_parent(root,relative) as (parent,leaf):
        if not stat.S_ISREG(os.stat(leaf,dir_fd=parent,follow_symlinks=False).st_mode):
            raise ContractError('forbidden_path','path')
    return root / relative


def read_safe(root: Path, relative: str, limit: int) -> bytes:
    """Bound allocation and reject symlinks in every directory component."""
    validate_relative(relative)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError("unsupported_environment", "nofollow_reader")
    if type(limit) is not int or limit < 0: raise ContractError(field='read_limit')
    with safe_parent(root, relative) as (parent_fd, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode): raise ContractError('forbidden_path', 'non_regular_file')
            if before.st_size > limit: raise ContractError('scope_limit', 'file_bytes')
            chunks, size = [], 0
            while size <= limit:
                chunk = os.read(fd, min(64 * 1024, limit + 1 - size))
                if not chunk: break
                chunks.append(chunk); size += len(chunk)
            after = os.fstat(fd); leaf_info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if size > limit: raise ContractError('scope_limit', 'file_bytes')
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (after.st_dev, after.st_ino) != (leaf_info.st_dev, leaf_info.st_ino) or size != after.st_size):
                raise ContractError('snapshot_unstable', 'file_changed')
            return b''.join(chunks)
        finally: os.close(fd)


class RepositoryScope:
    def inventory(self, root: Path, profile: ScopeProfile) -> ScopeManifest:
        from dataclasses import asdict
        from .validation import decode_record
        profile = decode_record(ScopeProfile, asdict(profile))
        root = Path(root).absolute()
        manifest = ScopeManifest(scope_version=profile.version, profile=profile)
        if not root.is_dir():
            manifest.errors.append(RunError("unreadable_file", "scope_root"))
            return manifest
        names = {}
        try: entries = list(walk_entries(root, profile))
        except ContractError as exc:
            manifest.errors.append(RunError(exc.code, exc.field)); return manifest
        for rel, kind, size in entries:
            if rel == ".":
                manifest.errors.append(RunError("unreadable_file", "scope_root"))
                continue
            item = FileEntry(rel, kind, size, "python" if Path(rel).suffix == ".py" else "text")
            reason = exclusion(rel, profile)
            if reason or kind in ("symlink", "special"):
                item.reason = reason or kind
                manifest.excluded.append(item)
                continue
            if kind == "directory":
                continue
            key = rel.casefold()
            if key in names and names[key] != rel:
                item.reason = "case_collision"
                manifest.unsupported.append(item)
                manifest.errors.append(RunError("case_collision", rel))
                continue
            names[key] = rel
            if kind == "unreadable":
                item.reason = "unreadable_file"
                manifest.unreadable.append(item)
            elif Path(rel).suffix.lower() not in TEXT_SUFFIXES:
                item.reason = "unsupported_language"
                manifest.unsupported.append(item)
            elif size > profile.max_file_bytes:
                item.reason = "scope_limit"
                manifest.unsupported.append(item)
                manifest.errors.append(RunError("scope_limit", rel))
            else:
                manifest.eligible.append(item)
        python_files = [f for f in manifest.eligible if f.language == "python"]
        if len(python_files) > profile.max_python_files or sum(f.size_bytes for f in manifest.eligible) > profile.max_text_bytes:
            manifest.errors.append(RunError("scope_limit", "files_or_bytes"))
        if not manifest.errors:
            for item in list(manifest.eligible):
                try:
                    resolve_allowed(root, item.path, manifest)
                    data = read_safe(root, item.path, profile.max_file_bytes)
                    if len(data) > profile.max_file_bytes:
                        raise ContractError("scope_limit", "changed_size")
                    manifest.bytes_read += len(data)
                    item.size_bytes = len(data)
                    item.lines = len(data.splitlines())
                except OSError:
                    item.reason = "unreadable_file"
                    manifest.eligible.remove(item)
                    manifest.unreadable.append(item)
                except ContractError as exc:
                    manifest.errors.append(RunError(exc.code, item.path))
                    break
            if sum(f.lines or 0 for f in manifest.eligible if f.language == "python") > profile.max_python_lines or manifest.bytes_read > profile.max_text_bytes:
                manifest.errors.append(RunError("scope_limit", "lines_or_bytes"))
        return manifest

    def prepare(self, task: TaskSpec, root: Path, profile: ScopeProfile):
        from .snapshots import SnapshotStore
        scope = self.inventory(root, profile)
        bundle = SnapshotStore().capture(root, scope)
        task.snapshot_ref = bundle.ref
        return bundle
