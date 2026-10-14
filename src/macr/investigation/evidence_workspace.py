"""Explicit outside-target storage and a narrow parent-owned receipt boundary."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

from macr.memory.atomic_file import atomic_write
from .evidence_budget import BudgetExceeded, EvidenceBudget
from .evidence_profiles import EvidenceProfile, load_json
from .scope import open_directory, read_safe, safe_parent, validate_workspace_path
from .validation import ContractError, validate_relative


def encode_bounded(value, limit, *, budget=None, stage='serialize', limit_field='output_bytes'):
    data = bytearray()
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        if budget:
            budget.check(stage)
        raw = chunk.encode('utf-8')
        if len(data) + len(raw) > limit:
            raise BudgetExceeded(limit_field, stage, limit, len(data) + len(raw))
        data.extend(raw)
    return bytes(data)


def storage_root(root, allowed_root, *, budget=None):
    """The caller explicitly grants an allowed subtree; links never grant access."""
    root, allowed_root = Path(root).absolute(), Path(allowed_root).absolute()
    if not root.is_relative_to(allowed_root):
        raise ContractError('forbidden_path', 'storage_root')
    try:
        with open_directory(allowed_root, create=True, identities=budget.anchors if budget else None):
            pass
        with open_directory(root, create=True, identities=budget.anchors if budget else None):
            pass
    except OSError:
        raise ContractError('forbidden_path', 'storage_directory') from None
    return root


def safe_write(root, relative, data, *, limit, budget=None, stage='storage_write', limit_field='output_bytes', publication=False):
    validate_relative(relative)
    if type(data) is not bytes or type(limit) is not int or limit <= 0:
        raise ContractError(field='storage_payload')
    if len(data) > limit:
        raise BudgetExceeded(limit_field, stage, limit, len(data))
    if budget:
        budget.check(stage)
    with safe_parent(root, relative, create=True, identities=budget.anchors if budget else None) as (parent_fd, leaf):
        if budget:
            info = os.fstatvfs(parent_fd)
            budget.minimum('min_free_bytes', info.f_bavail * info.f_frsize, stage)
            # Account for allocation units before writing; actual tree allocation is checked at publication.
            estimate = ((len(data) + info.f_frsize - 1) // info.f_frsize) * info.f_frsize
            if budget.usage.get('max_allocated_bytes', 0) + estimate > budget.profile.max_allocated_bytes:
                raise BudgetExceeded('max_allocated_bytes', stage, budget.profile.max_allocated_bytes, budget.usage.get('max_allocated_bytes', 0) + estimate)
        try:
            previous = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(previous.st_mode):
                raise ContractError('forbidden_path', 'storage_leaf')
            previous_allocation = previous.st_blocks * 512
        except FileNotFoundError:
            previous_allocation = 0
        if budget:
            budget.check('prepublication' if publication else stage)
        callback = budget.cancelled if budget else None
        def before_replace():
            # Temp write/fsync stays cancellable; replacement begins publication.
            budget.check('prepublication')
            budget.cancelled = None
        try:
            if publication and budget:
                atomic_write(Path(leaf), data, dir_fd=parent_fd, before_replace=before_replace)
            else:
                atomic_write(Path(leaf), data, dir_fd=parent_fd)
            if budget:
                allocated = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False).st_blocks * 512
                budget.observe('max_allocated_bytes', max(0, budget.usage.get('max_allocated_bytes', 0) + allocated - previous_allocation), stage)
                budget.usage['output_bytes'] = budget.usage.get('output_bytes', 0) + len(data)
                budget.check(stage)
        finally:
            if publication and budget:
                budget.cancelled = callback


def check_storage(root, budget, stage='storage_guard', *, live=False):
    """Strict by default; a live atomic temp rename permits one charged strict rescan."""
    budget.check(stage)
    pending = [Path(root)]
    allocated = 0
    def charge_name(name):
        name_bytes = len(name.encode('utf-8')) + 128
        budget.usage['guard_metadata_entries'] = budget.usage.get('guard_metadata_entries', 0) + 1
        budget.usage['guard_metadata_bytes'] = budget.usage.get('guard_metadata_bytes', 0) + name_bytes
        budget.charge('max_metadata_entries', 1, stage)
        budget.charge('max_metadata_bytes', name_bytes, stage)
    while pending:
        directory = pending.pop()
        with open_directory(directory, identities=budget.anchors) as fd, os.scandir(fd) as entries:
            allocated += os.fstat(fd).st_blocks * 512
            budget.observe('max_allocated_bytes', allocated, stage)
            for entry in entries:
                budget.check(stage)
                charge_name(entry.name)
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    match = re.fullmatch(r'(.+)-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tmp', entry.name)
                    if not live or match is None:
                        raise
                    budget.check(stage)
                    charge_name(match[1])
                    replacement = os.stat(match[1], dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(replacement.st_mode):
                        raise ContractError('forbidden_path', 'storage_guard_entry')
                    budget.observe('max_allocated_bytes', max(allocated, replacement.st_blocks * 512), stage)
                    # Restart the tree once; a second missing entry fails closed.
                    return check_storage(root, budget, stage, live=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(directory / entry.name)
                elif stat.S_ISREG(info.st_mode):
                    allocated += info.st_blocks * 512
                else:
                    raise ContractError('forbidden_path', 'storage_guard_entry')
                budget.observe('max_allocated_bytes', allocated, stage)
    budget.observe('max_allocated_bytes', allocated, stage)
    return allocated


class EvidenceWorkspace:
    def __init__(self, repo, out, profile: EvidenceProfile, *, budget=None):
        self._configure(repo, out, profile, budget)
        self.token = str(uuid4())
        try:
            with open_directory(self.root.parent, identities=self.budget.anchors) as parent_fd:
                try:
                    os.mkdir(self.root.name, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    raise ContractError('forbidden_path', 'output_exists') from None
                created = os.stat(self.root.name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(created.st_mode):
                    raise ContractError('forbidden_path', 'workspace_creation_identity')
                self.identity = (created.st_dev, created.st_ino)
                root_fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    opened = os.fstat(root_fd)
                    named = os.stat(self.root.name, dir_fd=parent_fd, follow_symlinks=False)
                    if self.identity != (opened.st_dev, opened.st_ino) or self.identity != (named.st_dev, named.st_ino):
                        raise ContractError('forbidden_path', 'workspace_creation_identity')
                    self.budget.anchors[str(self.root)] = self.identity
                    # The first pathname reopen is already bound to the observed created inode and parent.
                    with open_directory(self.root, identities=self.budget.anchors):
                        pass
                finally:
                    os.close(root_fd)
            self._marker = encode_bounded({'repo': str(self.repo), 'out': str(self.root), 'token': self.token,
                                           'device': self.identity[0], 'inode': self.identity[1],
                                           'repo_device': self.repo_identity[0], 'repo_inode': self.repo_identity[1]}, profile.max_control_bytes,
                                          stage='workspace_create', limit_field='max_control_bytes')
            safe_write(self.root, '.macr-owner.json', self._marker, limit=profile.max_control_bytes, budget=self.budget, stage='workspace_create', limit_field='max_control_bytes')
            for folder in ('snapshots', 'index', 'pending'):
                with safe_parent(self.root, folder, identities=self.budget.anchors) as (parent_fd, leaf):
                    os.mkdir(leaf, 0o700, dir_fd=parent_fd)
            self.check_storage('workspace_create')
        except OSError:
            raise ContractError('storage_unavailable', 'workspace_create') from None

    def _configure(self, repo, out, profile, budget):
        profile.to_dict()
        self.repo, self.root = Path(repo).absolute(), Path(out).absolute()
        self.profile, self.budget = profile, budget or EvidenceBudget(profile)
        validate_workspace_path(self.repo)
        validate_workspace_path(self.root, output=True)
        if self.root.is_relative_to(self.repo) or self.repo.is_relative_to(self.root):
            raise ContractError('forbidden_path', 'output_in_target')
        try:
            repo_ancestry, output_ancestry = {}, {}
            with open_directory(self.repo, identities=self.budget.anchors, observed=repo_ancestry) as fd:
                info = os.fstat(fd)
                self.repo_identity = (info.st_dev, info.st_ino)
                self.budget.anchors[str(self.repo)] = self.repo_identity
            with open_directory(self.root.parent, identities=self.budget.anchors, observed=output_ancestry) as fd:
                if self.repo_identity in output_ancestry.values():
                    raise ContractError('forbidden_path', 'output_in_target')
                try:
                    existing = os.stat(self.root.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (existing.st_dev, existing.st_ino) in repo_ancestry.values():
                        raise ContractError('forbidden_path', 'output_contains_target')
                parent = os.fstat(fd)
                self.budget.anchors[str(self.root.parent)] = (parent.st_dev, parent.st_ino)
        except OSError:
            raise ContractError('forbidden_path', 'workspace_ancestor') from None
        self.snapshots_root, self.index_root = self.root / 'snapshots', self.root / 'index'

    @classmethod
    def reopen(cls, repo, out, profile, token, *, budget=None):
        """Reopen only the marker's exact original directory, never an arbitrary existing out."""
        self = cls.__new__(cls)
        self._configure(repo, out, profile, budget)
        try:
            with open_directory(self.root) as root_fd:
                info = os.fstat(root_fd)
                self.identity = (info.st_dev, info.st_ino)
            self._marker = read_safe(self.root, '.macr-owner.json', profile.max_control_bytes, budget=self.budget, stage='workspace_reopen')
            marker = load_json(self._marker.decode('utf-8'), max_bytes=profile.max_control_bytes, max_depth=profile.max_input_depth)
            expected = {'repo': str(self.repo), 'out': str(self.root), 'token': token,
                        'device': self.identity[0], 'inode': self.identity[1],
                        'repo_device': self.repo_identity[0], 'repo_inode': self.repo_identity[1]}
            if type(token) is not str or marker != expected:
                raise ContractError('forbidden_path', 'workspace_owner')
            self.token = token
            self.budget.anchors[str(self.root)] = self.identity
            return self
        except (OSError, ValueError, TypeError):
            raise ContractError('forbidden_path', 'workspace_owner') from None

    def _owned(self):
        try:
            with open_directory(self.root, identities=self.budget.anchors) as fd:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != self.identity:
                    raise ContractError('forbidden_path', 'workspace_identity')
            marker = read_safe(self.root, '.macr-owner.json', self.profile.max_control_bytes, budget=self.budget, stage='workspace_owner')
            if marker != self._marker:
                raise ContractError('forbidden_path', 'workspace_owner')
        except OSError:
            raise ContractError('forbidden_path', 'workspace_owner') from None

    def write(self, relative, data, *, limit=None, limit_field='output_bytes'):
        self._owned()
        if relative in ('.macr-owner.json', 'COMMITTED'):
            raise ContractError('forbidden_path', 'reserved_storage_leaf')
        safe_write(self.root, relative, data, limit=self.profile.max_report_bytes if limit is None else limit,
                   budget=self.budget, stage='workspace_write', limit_field=limit_field)
        self._owned()

    def read(self, relative, *, limit):
        self._owned()
        data = read_safe(self.root, relative, limit, budget=self.budget, stage='workspace_read')
        self._owned()
        return data

    def check_storage(self, stage='storage_guard', *, live=False):
        self._owned()
        return check_storage(self.root, self.budget, stage, live=live)

    def commit(self, receipt: bytes):
        """Parent primitive only: caller must first prove worker exit and final report/source binding."""
        self._owned()
        if type(receipt) is not bytes or len(receipt) > self.profile.max_control_bytes:
            raise ContractError(field='receipt_bytes')
        details = load_json(receipt.decode('utf-8'), max_bytes=self.profile.max_control_bytes, max_depth=self.profile.max_input_depth)
        if type(details) is dict and 'documents' in details:
            def check_identity(relative, cap, expected):
                if (type(expected) is not dict or set(expected) != {'sha256', 'size_bytes'}
                        or type(expected['sha256']) is not str or len(expected['sha256']) != 64
                        or type(expected['size_bytes']) is not int or expected['size_bytes'] < 0):
                    raise ContractError('citation_invalid', 'publication_identity')
                raw = self.read(relative, limit=cap)
                if len(raw) != expected['size_bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                    raise ContractError('citation_invalid', 'publication_identity')
            documents = details['documents']
            if type(documents) is not dict or set(documents) != {'report.json', 'report.md', 'worker.log'}:
                raise ContractError('citation_invalid', 'publication_documents')
            for name, cap in (('report.json', self.profile.max_report_bytes),
                              ('report.md', self.profile.max_markdown_bytes), ('worker.log', self.profile.max_log_bytes)):
                check_identity('pending/' + name, cap, documents[name])
            source = details.get('source')
            if (type(source) is not dict or set(source) != {'verified', 'captured_source_verified', 'content_id', 'manifest'}
                    or source['verified'] is not True or type(source['captured_source_verified']) is not bool):
                raise ContractError('citation_invalid', 'publication_source')
            if source['captured_source_verified']:
                manifest = source['manifest']
                if type(manifest) is not dict or set(manifest) != {'relative', 'sha256', 'size_bytes'}:
                    raise ContractError('citation_invalid', 'publication_manifest')
                relative = manifest['relative']
                validate_relative(relative)
                parts = relative.split('/')
                if len(parts) != 3 or parts[0] != 'snapshots' or parts[2] != 'manifest.json' or manifest['sha256'] != source['content_id']:
                    raise ContractError('citation_invalid', 'publication_manifest')
                check_identity(relative, self.profile.max_scope_manifest_bytes,
                               {'sha256': manifest['sha256'], 'size_bytes': manifest['size_bytes']})
            elif source['content_id'] is not None or source['manifest'] is not None:
                raise ContractError('citation_invalid', 'publication_source')
        else:
            self.read('pending/report.json', limit=self.profile.max_report_bytes)
            self.read('pending/report.md', limit=self.profile.max_markdown_bytes)
        self.check_storage('publication_guard')
        with safe_parent(self.root, 'COMMITTED', identities=self.budget.anchors) as (fd, leaf):
            try:
                os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ContractError('storage_unavailable', 'already_committed')
        self.budget.check('prepublication')
        try:
            safe_write(self.root, 'COMMITTED', receipt, limit=self.profile.max_control_bytes,
                       budget=self.budget, stage='publication', publication=True)
        except BaseException:
            # A new receipt is not valid if directory synchronization or the final budget check failed.
            with safe_parent(self.root, 'COMMITTED', identities=self.budget.anchors) as (fd, leaf):
                try:
                    os.unlink(leaf, dir_fd=fd)
                except FileNotFoundError:
                    pass
            raise
