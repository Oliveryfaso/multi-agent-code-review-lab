"""One inventory/policy for all readers; exclusions are application policy, not Git."""
from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path

from .records import FileEntry, RunError, ScopeManifest, ScopeProfile, TaskSpec
from .validation import ContractError, validate_relative

EXCLUDED_DIRS = {".git", ".hg", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
                 ".ruff_cache", ".macr_cache", ".cache", ".tox", "artifacts", "traces", "patches", "reports",
                 ".aws", ".ssh", ".codex", ".agents", "secrets", "credentials", "oracles", "oracle"}
TEXT_SUFFIXES = {".py", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".json", ".md", ".txt"}
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*credentials*", "*secret*.json")


def exclusion(relative: str, profile: ScopeProfile) -> str:
    parts = relative.split("/")
    if any(p in EXCLUDED_DIRS for p in parts):
        return "excluded_subtree"
    if any(p.startswith("._") for p in parts):
        return "appledouble"
    if any(fnmatch.fnmatchcase(p.lower(), pattern) for p in parts for pattern in SECRET_PATTERNS):
        return "secret_path"
    if any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(p, pattern) for pattern in profile.exclude_patterns for p in parts):
        return "configured_exclusion"
    return ""


def walk_entries(root: Path):
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            yield str(directory.relative_to(root)), "unreadable", 0
            continue
        for entry in entries:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                yield rel, "unreadable", 0
                continue
            kind = "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "special"
            yield rel, kind, info.st_size
            if kind == "directory" and not exclusion(rel, ScopeProfile()):
                pending.append(path)


def resolve_allowed(root: Path, relative: str, scope: ScopeManifest) -> Path:
    validate_relative(relative)
    if scope.errors or relative not in {f.path for f in scope.eligible}:
        raise ContractError("forbidden_path", "scope")
    root = root.resolve()
    path = root / relative
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise ContractError("forbidden_path", "symlink")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ContractError("forbidden_path", "path")
    return path


def read_safe(root: Path, relative: str, limit: int) -> bytes:
    """Bound allocation and reject symlinks in every directory component."""
    validate_relative(relative)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError("unsupported_environment", "nofollow_reader")
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(root, directory_flags))
        parts = relative.split("/")
        for part in parts[:-1]:
            descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptors[-1])
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ContractError("scope_limit", "file_bytes")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        if size > limit:
            raise ContractError("scope_limit", "file_bytes")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ContractError("snapshot_unstable", "file_changed")
        return b"".join(chunks)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class RepositoryScope:
    def inventory(self, root: Path, profile: ScopeProfile) -> ScopeManifest:
        from dataclasses import asdict
        from .validation import decode_record
        profile = decode_record(ScopeProfile, asdict(profile))
        root = root.resolve()
        manifest = ScopeManifest(scope_version=profile.version, profile=profile)
        if not root.is_dir():
            manifest.errors.append(RunError("unreadable_file", "scope_root"))
            return manifest
        names = {}
        for rel, kind, size in walk_entries(root):
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
