"""Content-bound sanitized snapshots. Hashing here serves integrity only."""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .records import ActionUsage, CapturedFile, ContentManifest, ScopeManifest, SnapshotBundle, SnapshotRef
from .scope import TEXT_SUFFIXES, exclusion, read_safe, resolve_allowed, walk_entries
from .validation import ContractError, decode_record, validate_identifier, validate_relative
from .source_text import raw_physical_line_count
from .evidence_profiles import EvidenceProfile, load_json
from .evidence_budget import BudgetExceeded, EvidenceBudget
from .evidence_workspace import check_storage, encode_bounded, safe_write, storage_root
from .scope import safe_parent


class SnapshotError(ContractError):
    pass


class _CaptureBudgetExceeded(BudgetExceeded, SnapshotError):
    """Keep the legacy SnapshotError catch boundary with structured budget details."""


def canonical(manifest: ContentManifest) -> bytes:
    return json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class SnapshotStore:
    def __init__(self, root: Path | None = None, *, allowed_root=None, profile=None, budget=None):
        self.allowed = Path(allowed_root).absolute() if allowed_root is not None else (Path(__file__).resolve().parents[3] / 'artifacts').resolve()
        self.profile = profile or (budget.profile if budget else EvidenceProfile())
        self.budget = budget or (EvidenceBudget(self.profile) if profile is not None else None)
        self.root = storage_root(root or self.allowed / 'snapshots', self.allowed, budget=self.budget)
        self.last_usage = ActionUsage()

    def _read(self, root, relative, limit, stage):
        return read_safe(root, relative, limit, budget=self.budget, stage=stage,
                         limit_field='max_scope_manifest_bytes' if stage == 'manifest_read' else 'max_file_bytes') if self.budget else read_safe(root, relative, limit)

    def _inventory_matches(self, root: Path, scope: ScopeManifest):
        observed = {rel for rel, kind, size in walk_entries(root, scope.profile, budget=self.budget) if kind == "file" and not exclusion(rel, scope.profile)
                    and Path(rel).suffix.lower() in TEXT_SUFFIXES and size <= scope.profile.max_file_bytes}
        known_unreadable = {f.path for f in scope.unreadable}
        if observed - known_unreadable != {f.path for f in scope.eligible}:
            raise SnapshotError("snapshot_unstable", "inventory")

    def capture(self, root: Path, scope: ScopeManifest) -> SnapshotBundle:
        if scope.errors:
            raise SnapshotError(scope.errors[0].code, "scope")
        if self.budget:
            check_storage(self.allowed, self.budget, 'capture_storage_guard')
        self._inventory_matches(root, scope)
        scope = deepcopy(scope)
        snapshot_id = str(uuid4())
        folder = self.root / snapshot_id
        files_root = folder / "files"
        with safe_parent(self.root, snapshot_id, identities=self.budget.anchors if self.budget else None) as (parent_fd, leaf):
            os.mkdir(leaf, 0o700, dir_fd=parent_fd)
        with safe_parent(folder, 'files', identities=self.budget.anchors if self.budget else None) as (parent_fd, leaf):
            os.mkdir(leaf, 0o700, dir_fd=parent_fd)
        captured, manifest_files = {}, []
        usage = ActionUsage()
        captured_bytes, python_lines = 0, 0
        try:
            for entry in sorted(scope.eligible, key=lambda item: item.path):
                resolve_allowed(root, entry.path, scope)
                data = self._read(root, entry.path, scope.profile.max_file_bytes, 'capture_source')
                usage.bytes_read += len(data)
                entry.size_bytes, entry.lines = len(data), raw_physical_line_count(data)
                captured_bytes += entry.size_bytes
                python_lines += entry.lines if entry.language == 'python' else 0
                for field, observed in (('max_text_bytes', captured_bytes), ('max_python_lines', python_lines)):
                    limit = getattr(scope.profile, field)
                    if observed > limit:
                        raise _CaptureBudgetExceeded(field, 'capture_source', limit, observed)
                digest = hashlib.sha256(data).hexdigest()
                captured[entry.path] = CapturedFile(entry, data, digest)
                safe_write(files_root, entry.path, data, limit=scope.profile.max_file_bytes, budget=self.budget, stage='capture_write')
                manifest_files.append({"path": entry.path, "kind": "file", "size_bytes": len(data), "sha256": digest,
                                       "language": entry.language, "mode_intent": "non_executable"})
            for relative, item in captured.items():
                data = self._read(root, relative, scope.profile.max_file_bytes, 'capture_reverify')
                usage.bytes_read += len(data)
                if data != item.content:
                    raise SnapshotError("snapshot_unstable", "capture_changed")
            self._inventory_matches(root, scope)
        except OSError:
            raise SnapshotError("snapshot_unstable", "capture_io") from None
        except BudgetExceeded:
            raise
        except ContractError as exc:
            raise SnapshotError(exc.code, "capture") from None
        finally:
            self.last_usage = usage
        scope.bytes_read += usage.bytes_read
        manifest = ContentManifest(scope.scope_version, manifest_files, asdict(scope.profile))
        data = encode_bounded(asdict(manifest), self.profile.max_scope_manifest_bytes, budget=self.budget, stage='manifest_encode', limit_field='max_scope_manifest_bytes')
        manifest_path = folder / "manifest.json"
        try:
            if self.budget:
                check_storage(self.allowed, self.budget, 'capture_storage_guard')
            safe_write(folder, 'manifest.json', data, limit=self.profile.max_scope_manifest_bytes, budget=self.budget, stage='manifest_write', limit_field='max_scope_manifest_bytes')
            scope_data = encode_bounded(asdict(scope), self.profile.max_scope_manifest_bytes, budget=self.budget, stage='scope_encode', limit_field='max_scope_manifest_bytes')
            safe_write(folder, 'scope.json', scope_data, limit=self.profile.max_scope_manifest_bytes, budget=self.budget, stage='scope_write', limit_field='max_scope_manifest_bytes')
        except OSError:
            raise SnapshotError("storage_unavailable", "snapshot_write") from None
        ref = SnapshotRef(snapshot_id, hashlib.sha256(data).hexdigest(), str(manifest_path), str(files_root), scope.scope_version)
        return SnapshotBundle(ref, scope, captured)

    def _manifest(self, ref: SnapshotRef) -> ContentManifest:
        validate_identifier(ref.snapshot_id)
        folder = self.root / ref.snapshot_id
        if Path(ref.root_ref).absolute() != folder / 'files' or Path(ref.content_manifest_ref).absolute() != folder / 'manifest.json':
            raise SnapshotError("stale_snapshot", "snapshot_paths")
        if folder.is_symlink() or (folder / "files").is_symlink():
            raise SnapshotError("stale_snapshot", "snapshot_symlink")
        try:
            raw = self._read(folder, 'manifest.json', self.profile.max_scope_manifest_bytes, 'manifest_read')
            self.last_usage.bytes_read += len(raw)
            manifest = decode_record(ContentManifest, load_json(raw.decode('utf-8'), max_bytes=self.profile.max_scope_manifest_bytes, max_depth=self.profile.max_input_depth))
            if manifest.scope_version != ref.scope_version or hashlib.sha256(canonical(manifest)).hexdigest() != ref.content_id:
                raise SnapshotError("stale_snapshot", "content_identity")
            paths = []
            for entry in manifest.files:
                validate_relative(entry["path"])
                if entry["kind"] != "file" or type(entry["size_bytes"]) is not int or not 0 <= entry["size_bytes"] <= manifest.scope_profile['max_file_bytes']:
                    raise SnapshotError("stale_snapshot", "manifest_entry")
                paths.append(entry["path"])
            if paths != sorted(set(paths)):
                raise SnapshotError("stale_snapshot", "manifest_order")
            return manifest
        except BudgetExceeded:
            raise
        except (OSError, ValueError, KeyError, TypeError, ContractError) as error:
            if isinstance(error, ContractError) and error.code == 'cancelled':
                raise
            raise SnapshotError("stale_snapshot", "manifest") from None

    def read_verified(self, ref: SnapshotRef, relative: str) -> bytes:
        self.last_usage = ActionUsage()
        manifest = self._manifest(ref)
        entry = next((item for item in manifest.files if item["path"] == relative), None)
        if entry is None:
            raise SnapshotError("forbidden_path", "snapshot_file")
        return self._read_entry(ref, entry)

    def _read_entry(self, ref: SnapshotRef, entry: dict) -> bytes:
        try:
            data = self._read(Path(ref.root_ref), entry['path'], entry['size_bytes'], 'snapshot_read')
        except BudgetExceeded as error:
            if error.field == 'max_file_bytes':
                raise SnapshotError('stale_snapshot', 'snapshot_file') from None
            raise
        except (OSError, ContractError) as error:
            if isinstance(error, ContractError) and error.code == 'cancelled':
                raise
            raise SnapshotError("stale_snapshot", "snapshot_file") from None
        self.last_usage.bytes_read += len(data)
        if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SnapshotError("stale_snapshot", "file_content")
        return data

    def verify(self, ref: SnapshotRef, files: list[str] | None = None) -> None:
        self.last_usage = ActionUsage(tool_calls=1)
        manifest = self._manifest(ref)
        entries = {entry['path']: entry for entry in manifest.files}
        for relative in files if files is not None else [f["path"] for f in manifest.files]:
            if relative not in entries:
                raise SnapshotError('forbidden_path', 'snapshot_file')
            self._read_entry(ref, entries[relative])

    def export_verified(self, ref: SnapshotRef, destination: Path) -> ContentManifest:
        destination = destination.absolute()
        if not destination.is_relative_to(self.allowed):
            raise SnapshotError("forbidden_path", "export_destination")
        self.verify(ref)
        manifest = self._manifest(ref)
        with safe_parent(self.allowed, destination.relative_to(self.allowed).as_posix(), create=True, identities=self.budget.anchors if self.budget else None) as (parent_fd, leaf):
            os.mkdir(leaf, 0o700, dir_fd=parent_fd)
        for entry in manifest.files:
            data = self.read_verified(ref, entry["path"])
            safe_write(destination, entry['path'], data, limit=max(1, entry['size_bytes']), budget=self.budget, stage='snapshot_export')
        return manifest
