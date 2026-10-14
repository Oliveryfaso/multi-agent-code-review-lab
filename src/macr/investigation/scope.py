"""One inventory/policy for all readers; exclusions are application policy, not Git."""
from __future__ import annotations

import fnmatch
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from .records import FileEntry, RunError, ScopeManifest, ScopeProfile, TaskSpec
from .validation import ContractError, validate_relative
from .source_text import raw_physical_line_count
from .evidence_budget import BudgetExceeded

EXCLUDED_DIRS = {".git", ".hg", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
                 ".ruff_cache", ".macr_cache", ".cache", ".tox", "artifacts", "traces", "patches", "reports",
                 ".aws", ".ssh", ".codex", ".agents", "secrets", "credentials", "oracles", "oracle"}
TEXT_SUFFIXES = {".py", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".json", ".md", ".txt"}
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*credentials*", "*secret*.json")
PRIVATE_DIRS = {'.git', '.hg', '.aws', '.ssh', '.codex', '.agents', 'secrets', 'credentials', 'oracles', 'oracle'}


def validate_workspace_path(path: Path, *, output=False):
    """Explicit roots do not grant protected ancestry; output containers have a separate policy."""
    parts = Path(path).absolute().parts
    excluded = PRIVATE_DIRS if output else EXCLUDED_DIRS
    if any(part.casefold() in excluded or part.startswith('._') or
           any(fnmatch.fnmatchcase(part.casefold(), pattern) for pattern in SECRET_PATTERNS)
           for part in parts):
        raise ContractError('forbidden_path', 'protected_output' if output else 'protected_source')


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


@contextmanager
def open_directory(path: Path, *, create=False, identities=None, observed=None):
    """Anchor every absolute component, including ancestors of the root."""
    path = Path(path).absolute()
    if '..' in path.parts or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise ContractError('forbidden_path', 'directory')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = [os.open(path.anchor, flags)]
    try:
        if observed is not None:
            info = os.fstat(descriptors[0])
            observed[path.anchor] = (info.st_dev, info.st_ino)
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
            current_path = Path(path.anchor).joinpath(*path.parts[1:len(descriptors)])
            expected = (identities or {}).get(str(current_path))
            if expected is not None or observed is not None:
                info = os.fstat(descriptors[-1])
                actual = (info.st_dev, info.st_ino)
                if observed is not None:
                    observed[str(current_path)] = actual
                if expected is not None and actual != expected:
                    raise ContractError('forbidden_path', 'directory_identity')
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def safe_parent(root: Path, relative: str, *, create=False, identities=None):
    validate_relative(relative)
    with open_directory(root, identities=identities) as root_fd:
        descriptors = []
        current = root_fd
        try:
            for part in relative.split('/')[:-1]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(current)
            yield current, relative.split('/')[-1]
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def walk_entries(root: Path, profile: ScopeProfile | None = None, *, budget=None):
    from .evidence_profiles import EvidenceProfile
    profile = profile or ScopeProfile()
    limits = budget.profile if budget else EvidenceProfile()
    pending = [('', root)]
    count, metadata_bytes = 0, 0
    while pending:
        directory_rel, directory = pending.pop()
        try:
            # Consume one bounded directory before sorting; never materialize an unbounded scandir.
            with open_directory(directory, identities=budget.anchors if budget else None) as directory_fd, os.scandir(directory_fd) as iterator:
                entries = []
                for entry in iterator:
                    rel = '/'.join(filter(None, (directory_rel, entry.name)))
                    count += 1
                    metadata_bytes += len(rel.encode('utf-8')) + 128
                    for field, observed in (('max_metadata_entries', count), ('max_metadata_bytes', metadata_bytes)):
                        if budget:
                            budget.charge(field, 1 if field == 'max_metadata_entries' else len(rel.encode('utf-8')) + 128, 'inventory_metadata')
                        elif observed > getattr(limits, field):
                            raise BudgetExceeded(field, 'inventory_metadata', getattr(limits, field), observed)
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        entries.append((rel, 'unreadable', 0))
                        continue
                    kind = 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'special'
                    entries.append((rel, kind, info.st_size))
        except OSError:
            yield directory_rel or '.', 'unreadable', 0
            continue
        for rel, kind, size in sorted(entries):
            yield rel, kind, size
            if kind == 'directory' and not exclusion(rel, profile):
                pending.append((rel, root / rel))


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


def read_safe(root: Path, relative: str, limit: int, *, budget=None, stage='source_read', limit_field='max_file_bytes') -> bytes:
    """Bound allocation and reject symlinks in every directory component."""
    validate_relative(relative)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError("unsupported_environment", "nofollow_reader")
    if type(limit) is not int or limit < 0:
        raise ContractError(field='read_limit')
    if budget:
        budget.check(stage)
    with safe_parent(root, relative, identities=budget.anchors if budget else None) as (parent_fd, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ContractError('forbidden_path', 'non_regular_file')
            if before.st_size > limit:
                raise BudgetExceeded(limit_field, stage, limit, before.st_size)
            chunks, size = [], 0
            while size <= limit:
                if budget:
                    budget.check(stage)
                chunk = os.read(fd, min(64 * 1024, limit + 1 - size))
                if budget:
                    budget.charge('max_io_bytes', len(chunk), stage)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            after = os.fstat(fd)
            leaf_info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if size > limit:
                raise BudgetExceeded(limit_field, stage, limit, size)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or (after.st_dev, after.st_ino) != (leaf_info.st_dev, leaf_info.st_ino) or size != after.st_size:
                raise ContractError('snapshot_unstable', 'file_changed')
            return b''.join(chunks)
        finally:
            os.close(fd)


class RepositoryScope:
    def inventory(self, root: Path, profile: ScopeProfile, *, budget=None) -> ScopeManifest:
        from dataclasses import asdict
        from .validation import decode_record
        profile = decode_record(ScopeProfile, asdict(profile))
        root = Path(root).absolute()
        manifest = ScopeManifest(scope_version=profile.version, profile=profile)
        if not root.is_dir():
            manifest.errors.append(RunError("unreadable_file", "scope_root"))
            return manifest
        names = {}
        try:
            entries = iter(walk_entries(root, profile, budget=budget))
            while True:
                try:
                    rel, kind, size = next(entries)
                except StopIteration:
                    break
                self._add_entry(manifest, names, root, profile, rel, kind, size)
        except BudgetExceeded as error:
            manifest.errors.append(RunError(error.code, error.detail()))
        eligible = manifest.eligible
        limits = budget.profile if budget else None
        for field, observed, limit in (
            ('max_python_files', sum(f.language == 'python' for f in eligible), profile.max_python_files),
            ('max_text_files', len(eligible), limits.max_text_files if limits else profile.max_python_files),
            ('max_text_bytes', sum(f.size_bytes for f in eligible), profile.max_text_bytes),
        ):
            if observed > limit:
                manifest.errors.append(RunError('scope_limit', BudgetExceeded(field, 'scope_inventory', limit, observed).detail()))
        if not manifest.errors:
            for item in list(manifest.eligible):
                try:
                    resolve_allowed(root, item.path, manifest)
                    data = read_safe(root, item.path, profile.max_file_bytes, budget=budget, stage='scope_source') if budget else read_safe(root, item.path, profile.max_file_bytes)
                    manifest.bytes_read += len(data)
                    item.size_bytes = len(data)
                    item.lines = raw_physical_line_count(data)
                    for field, observed, limit in (
                        ('max_python_lines', sum(f.lines or 0 for f in manifest.eligible if f.language == 'python'), profile.max_python_lines),
                        ('max_text_bytes', manifest.bytes_read, profile.max_text_bytes),
                    ):
                        if observed > limit:
                            raise BudgetExceeded(field, 'scope_source', limit, observed)
                except OSError:
                    item.reason = 'unreadable_file'
                    manifest.eligible.remove(item)
                    manifest.unreadable.append(item)
                except ContractError as exc:
                    manifest.errors.append(RunError(exc.code, exc.detail() if isinstance(exc, BudgetExceeded) else exc.field))
                    break
        return manifest

    def _add_entry(self, manifest, names, root, profile, rel, kind, size):
        if rel == '.':
            manifest.errors.append(RunError('unreadable_file', 'scope_root'))
            return
        item = FileEntry(rel, kind, size, 'python' if Path(rel).suffix == '.py' else 'text')
        reason = exclusion(rel, profile)
        if reason or kind in ('symlink', 'special'):
            item.reason = reason or kind
            manifest.excluded.append(item)
            return
        key = rel.casefold()
        if key in names and names[key] != rel:
            item.reason = 'case_collision'
            manifest.unsupported.append(item)
            manifest.errors.append(RunError('case_collision', rel))
            return
        names[key] = rel
        if kind == 'directory':
            return
        if kind == 'unreadable':
            item.reason = 'unreadable_file'
            manifest.unreadable.append(item)
        elif Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            item.reason = 'unsupported_language'
            manifest.unsupported.append(item)
        elif size > profile.max_file_bytes:
            item.reason = 'scope_limit'
            manifest.unsupported.append(item)
            manifest.errors.append(RunError('scope_limit', BudgetExceeded('max_file_bytes', 'scope_inventory', profile.max_file_bytes, size).detail()))
        else:
            manifest.eligible.append(item)

    def prepare(self, task: TaskSpec, root: Path, profile: ScopeProfile):
        from .snapshots import SnapshotStore
        scope = self.inventory(root, profile)
        bundle = SnapshotStore().capture(root, scope)
        task.snapshot_ref = bundle.ref
        return bundle
