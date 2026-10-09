"""Content-bound sanitized snapshots. Hashing here serves integrity only."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .records import ActionUsage, CapturedFile, ContentManifest, ScopeManifest, SnapshotBundle, SnapshotRef
from .scope import TEXT_SUFFIXES, exclusion, read_safe, resolve_allowed, walk_entries
from .validation import ContractError, decode_record, validate_identifier, validate_relative


class SnapshotError(ContractError):
    pass


def canonical(manifest: ContentManifest) -> bytes:
    return json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class SnapshotStore:
    def __init__(self, root: Path | None = None):
        self.allowed = (Path(__file__).resolve().parents[3] / "artifacts").resolve()
        self.root = (root or self.allowed / "snapshots").resolve()
        if not self.root.is_relative_to(self.allowed):
            raise SnapshotError("forbidden_path", "snapshot_root")
        self.last_usage = ActionUsage()

    def _inventory_matches(self, root: Path, scope: ScopeManifest):
        observed = {rel for rel, kind, size in walk_entries(root) if kind == "file" and not exclusion(rel, scope.profile)
                    and Path(rel).suffix.lower() in TEXT_SUFFIXES and size <= scope.profile.max_file_bytes}
        known_unreadable = {f.path for f in scope.unreadable}
        if observed - known_unreadable != {f.path for f in scope.eligible}:
            raise SnapshotError("snapshot_unstable", "inventory")

    def capture(self, root: Path, scope: ScopeManifest) -> SnapshotBundle:
        if scope.errors:
            raise SnapshotError(scope.errors[0].code, "scope")
        self._inventory_matches(root, scope)
        scope = deepcopy(scope)
        snapshot_id = str(uuid4())
        folder = self.root / snapshot_id
        files_root = folder / "files"
        files_root.mkdir(parents=True, exist_ok=False)
        captured, manifest_files = {}, []
        usage = ActionUsage()
        try:
            for entry in sorted(scope.eligible, key=lambda item: item.path):
                resolve_allowed(root, entry.path, scope)
                data = read_safe(root, entry.path, scope.profile.max_file_bytes)
                usage.bytes_read += len(data)
                entry.size_bytes, entry.lines = len(data), len(data.splitlines())
                digest = hashlib.sha256(data).hexdigest()
                captured[entry.path] = CapturedFile(entry, data, digest)
                target = files_root / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                manifest_files.append({"path": entry.path, "kind": "file", "size_bytes": len(data), "sha256": digest,
                                       "language": entry.language, "mode_intent": "non_executable"})
            if sum(e.size_bytes for e in scope.eligible) > scope.profile.max_text_bytes or sum(e.lines or 0 for e in scope.eligible if e.language == "python") > scope.profile.max_python_lines:
                raise SnapshotError("scope_limit", "captured_scope")
            for relative, item in captured.items():
                data = read_safe(root, relative, scope.profile.max_file_bytes)
                usage.bytes_read += len(data)
                if data != item.content:
                    raise SnapshotError("snapshot_unstable", "capture_changed")
            self._inventory_matches(root, scope)
        except OSError:
            raise SnapshotError("snapshot_unstable", "capture_io") from None
        except ContractError as exc:
            raise SnapshotError(exc.code, "capture") from None
        finally:
            self.last_usage = usage
        scope.bytes_read += usage.bytes_read
        manifest = ContentManifest(scope.scope_version, manifest_files, asdict(scope.profile))
        data = canonical(manifest)
        manifest_path = folder / "manifest.json"
        try:
            manifest_path.write_bytes(data)
            (folder / "scope.json").write_text(json.dumps(asdict(scope), sort_keys=True, allow_nan=False), encoding="utf-8")
        except OSError:
            raise SnapshotError("storage_unavailable", "snapshot_write") from None
        ref = SnapshotRef(snapshot_id, hashlib.sha256(data).hexdigest(), str(manifest_path), str(files_root), scope.scope_version)
        return SnapshotBundle(ref, scope, captured)

    def _manifest(self, ref: SnapshotRef) -> ContentManifest:
        validate_identifier(ref.snapshot_id)
        folder = self.root / ref.snapshot_id
        if Path(ref.root_ref).resolve() != (folder / "files").resolve() or Path(ref.content_manifest_ref).resolve() != (folder / "manifest.json").resolve():
            raise SnapshotError("stale_snapshot", "snapshot_paths")
        if folder.is_symlink() or (folder / "files").is_symlink():
            raise SnapshotError("stale_snapshot", "snapshot_symlink")
        try:
            raw = read_safe(folder, "manifest.json", 4 * 1024 * 1024)
            self.last_usage.bytes_read += len(raw)
            manifest = decode_record(ContentManifest, json.loads(raw))
            if manifest.scope_version != ref.scope_version or hashlib.sha256(canonical(manifest)).hexdigest() != ref.content_id:
                raise SnapshotError("stale_snapshot", "content_identity")
            paths = []
            for entry in manifest.files:
                validate_relative(entry["path"])
                if entry["kind"] != "file" or type(entry["size_bytes"]) is not int or not 0 <= entry["size_bytes"] <= 512 * 1024 * 1024:
                    raise SnapshotError("stale_snapshot", "manifest_entry")
                paths.append(entry["path"])
            if paths != sorted(set(paths)):
                raise SnapshotError("stale_snapshot", "manifest_order")
            return manifest
        except (OSError, ValueError, KeyError, TypeError, ContractError):
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
            data = read_safe(Path(ref.root_ref), entry["path"], entry["size_bytes"])
        except (OSError, ContractError):
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
        destination = destination.resolve()
        if not destination.is_relative_to(self.allowed):
            raise SnapshotError("forbidden_path", "export_destination")
        self.verify(ref)
        manifest = self._manifest(ref)
        destination.mkdir(parents=True, exist_ok=False)
        for entry in manifest.files:
            data = self.read_verified(ref, entry["path"])
            target = destination / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return manifest
