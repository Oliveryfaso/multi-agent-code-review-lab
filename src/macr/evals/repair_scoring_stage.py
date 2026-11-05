"""Evaluator-only whole-file staging. No collection, execution or score import."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path

from macr.investigation import repair_loop as loop
from macr.investigation.repair_entry import score_handoff
from macr.investigation.repair_io import encode_bounded, safe_write
from macr.investigation.scope import read_safe, safe_parent, validate_workspace_path, paths_overlap as _overlap
from .repair_comparison import result_digest
from .repair_scoring_overlay import read_overlay

JSON_LIMIT = 2 * 1024 * 1024


def _inventory(files):
    return {path: {'bytes': len(body), 'sha256': sha256(body).hexdigest()}
            for path, body in sorted(files.items())}


def _resolved(path):
    try:
        return path.resolve()
    except RuntimeError:
        raise ValueError('comparison_scoring_path_invalid') from None


def stage_scoring(record, plan, cell_id, manifest, overlay_root, out, *, input_paths=()):
    """Revalidate generation provenance, then freeze two unexecuted scorer trees.

    Staged identities bind raw inventories separately from complete generation
    source IDs. Mutable local hashes are not independent attestation.
    """
    record = Path(record).absolute(); overlay_root = Path(overlay_root).absolute()
    out = Path(out).absolute()
    validate_workspace_path(out, output=True)
    if out != _resolved(out):
        raise ValueError('comparison_output_identity')
    for source in (record, overlay_root, *map(Path, input_paths)):
        source = _resolved(source.absolute())
        if _overlap(out,source):
            raise ValueError('comparison_scoring_output_overlap')
    packet = score_handoff(record, plan, cell_id)
    config = loop._read(record,'claim.json')['config']
    windows = config.get('source_windows')
    if windows != packet.get('source_windows'):
        raise ValueError('comparison_scoring_window_binding')
    full_originals = windows is not None
    original = loop._files(record/'sources/0',full_originals=full_originals)
    if loop.source_id(original,full_originals=full_originals) != packet['original_source_id']:
        raise ValueError('comparison_scoring_original_binding')
    overlay = read_overlay(overlay_root, manifest, plan, packet['cell']['task_id'],
        original_files=original, protected_paths=packet['protected_paths'],
        editable_paths=sorted(set(original)-set(packet['protected_paths'])))
    candidate = (None if packet['candidate_files'] is None else
                 {path: text.encode('utf-8') for path, text in packet['candidate_files'].items()})
    staged_original = {**original, **overlay}
    staged_candidate = None if candidate is None else {**candidate, **overlay}
    original_inventory = _inventory(staged_original)
    candidate_inventory = None if staged_candidate is None else _inventory(staged_candidate)
    staging = {'schema': 'macr.repair.scoring-stage.v1',
        'comparison_sha256': packet['comparison_sha256'], 'cell': packet['cell'],
        'packet_sha256': packet['packet_sha256'], 'overlay_sha256': result_digest(manifest),
        'generation_original_source_id': packet['original_source_id'],
        'generation_candidate_source_id': packet['result']['candidate_sha256'],
        'candidate_origin': packet['result']['candidate_origin'],
        'candidate_status': 'not_available' if candidate is None else 'unverified',
        'original_files': original_inventory, 'candidate_files': candidate_inventory,
        'staged_original_sha256': result_digest(original_inventory),
        'staged_candidate_sha256': None if candidate is None else result_digest(candidate_inventory),
        'overlays': [{'path':path, 'operation':'replace_protected' if path in original else 'add_test',
            'original_sha256':sha256(original[path]).hexdigest() if path in original else None,
            'overlay_sha256':sha256(body).hexdigest(), 'bytes':len(body)}
            for path,body in sorted(overlay.items())],
        'test_collection_status':'not_assessed', 'tests_executed':False,
        'scoring_selector_status':'not_provided', 'execution_status':'not_performed',
        'generation_receipt_applies_to_overlay':False,
        'scoring_credit_awarded':False, 'execution_authorized':False,
        'provenance_authenticated':False}
    if manifest['schema'] == 'macr.repair.scoring-overlay.v2':
        declarations = {entry['path']:entry['declared_path'] for task in manifest['tasks']
                        if task['task_id']==packet['cell']['task_id'] for entry in task['files']}
        for item in staging['overlays']:
            item['declared_path'] = declarations[item['path']]
    staging['staging_sha256'] = result_digest(staging)
    metadata = {'packet.json':packet, 'results.json':{cell_id:packet['result']},
                'overlay-manifest.json':manifest, 'staging.json':staging}
    prepared = {name:encode_bounded(value,JSON_LIMIT) for name,value in metadata.items()}
    if sum(map(len,prepared.values())) > JSON_LIMIT:
        raise ValueError('comparison_scoring_metadata_size')
    # Read and validate every input before the exclusive output claim. Partial
    # writes remain partial; staging.json is the last file, never a pass receipt.
    with safe_parent(out.parent,out.name) as (parent,leaf):
        os.mkdir(leaf,0o700,dir_fd=parent)
    for variant, files in (('original',staged_original),('candidate',staged_candidate)):
        if files is not None:
            for path,body in sorted(files.items()):
                safe_write(out,variant+'/'+path,body,limit=1024*1024)
    for name,body in prepared.items():
        if name != 'staging.json':
            safe_write(out,name,body,limit=JSON_LIMIT)
    # Read back the exact emitted inventories without importing test modules.
    for variant,files in (('original',staged_original),('candidate',staged_candidate)):
        if files is not None:
            for path,body in files.items():
                if read_safe(out,variant+'/'+path,len(body)) != body:
                    raise ValueError('comparison_scoring_write_binding')
    safe_write(out,'staging.json',prepared['staging.json'],limit=JSON_LIMIT)
    return staging
