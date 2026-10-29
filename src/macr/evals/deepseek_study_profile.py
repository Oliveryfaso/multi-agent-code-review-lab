"""Gold-free preparation for the proposed nine-question study; remote stays off."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import random
import re

from macr.evals.deepseek_policy_manifest import (DEFAULT_BUDGET, PROMPT_VERSION,
    _task, _read, _root, _relative, prepare_policy_manifest, load_policy_manifest)
from macr.evals.deepseek_policy_runner import _json_bytes, _write_exclusive
from macr.evals.deepseek_study_plan import build_fixed_plan
from macr.evals.deepseek_study_generation import V2_PROFILE, get_generation
from macr.investigation.scope import validate_workspace_path

PACK_SCHEMA = 'macr.deepseek.study.pack.v1'
SCHEMA = 'macr.deepseek.study.profile.v1'
STRATEGIES = ('direct_initial_3', 'fixed_read_3', 'adaptive_read_3', 'direct_full_3')
TASK_FIELDS = {'id', 'project_id', 'snapshot_root', 'question', 'scenario', 'assumptions',
    'authorized_files', 'initial_evidence_files', 'entrypoint', 'license'}
IMPLEMENTATION_FILES = (
    'src/macr/evals/deepseek_study_profile.py', 'src/macr/evals/deepseek_study_plan.py',
    'src/macr/evals/deepseek_study_runner.py', 'src/macr/evals/deepseek_study_batch.py',
    'src/macr/evals/deepseek_study_report.py', 'src/macr/evals/deepseek_policy_manifest.py',
    'src/macr/evals/deepseek_policy_runner.py', 'src/macr/evals/deepseek_policy_tools.py',
    'src/macr/evals/deepseek_policy_spend.py', 'src/macr/evals/deepseek_policy_batch.py',
    'src/macr/evals/diagnostic_compare.py', 'src/macr/providers/base.py',
    'src/macr/providers/deepseek.py', 'src/macr/providers/failure_evidence.py',
    'src/macr/providers/response_normalization.py', 'src/macr/investigation/scope.py',
    'src/macr/investigation/validation.py', 'src/macr/investigation/source_text.py',
    'src/macr/investigation/evidence_budget.py', 'src/macr/investigation/records.py',
    'scripts/run_deepseek_study_dryrun.py',
    'src/macr/evals/deepseek_study_live.py', 'scripts/run_deepseek_study_live.py',
    'src/macr/evals/deepseek_study_continuation.py',
    'src/macr/evals/deepseek_study_generation.py',
)
# Trusted product import closure, including package initializers. These hashes
# freeze existing dependencies; none of these modules execute target source.
IMPORTED_IMPLEMENTATION_FILES = tuple('src/macr/' + file for file in (
    '__init__.py', 'agents/__init__.py', 'agents/code_smell.py', 'agents/critic.py',
    'agents/final_reviewer.py', 'agents/monitor.py', 'agents/orchestrator.py',
    'agents/patch_ranker.py', 'agents/patcher.py', 'agents/planner.py',
    'agents/pr_reviewer.py', 'agents/router.py', 'agents/routing_policy.py',
    'agents/solver.py', 'contracts.py', 'costs.py', 'evals/__init__.py', 'evals/runner.py',
    'execution/__init__.py', 'execution/base.py', 'execution/results.py',
    'investigation/__init__.py', 'investigation/budget.py',
    'investigation/evidence_profiles.py', 'investigation/evidence_workspace.py',
    'investigation/index.py', 'investigation/queue.py', 'investigation/snapshots.py',
    'memory/__init__.py', 'memory/atomic_file.py', 'memory/board.py',
    'memory/evidence_store.py', 'memory/run_store.py', 'memory/trace_encoding.py',
    'memory/trace_store.py', 'memory/trace_summary.py', 'prompts.py',
    'providers/__init__.py', 'providers/local_http.py', 'providers/mock.py',
    'providers/profiles.py', 'runtime.py', 'schemas.py', 'tools/__init__.py',
    'tools/ast_tool.py', 'tools/code_graph.py', 'tools/git_tool.py',
    'tools/patch_verifier.py', 'tools/repo_map.py', 'tools/search.py',
    'tools/symbol_graph.py', 'tools/test_runner.py', 'tools/timing.py', 'workflow.py'))
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_HASH = re.compile('[0-9a-f]{64}\\Z')


class StudyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise StudyError(code)


def digest(value):
    return sha256(_json_bytes(value)).hexdigest()


def read_json(root, name, limit=262144):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(_read(root, name, limit), object_pairs_hook=unique,
            parse_constant=lambda _: require(False, 'nonfinite_json'))
    except (ValueError, UnicodeError, RecursionError):
        raise StudyError('study_json_invalid') from None


def validate_budget(value=None):
    require(value is None or type(value) is dict, 'study_budget_fields_invalid')
    value = dict({'max_requests': 108, 'max_usd': '2.000000'} if value is None else value)
    require(value.get('remote_enabled', False) is False, 'remote_disabled')
    require(set(value) == {'max_requests', 'max_usd'}, 'study_budget_fields_invalid')
    from macr.evals.deepseek_policy_spend import _parse_usd
    require(type(value['max_requests']) is int and 1 <= value['max_requests'] <= 108,
            'study_request_ceiling_invalid')
    amount = _parse_usd(value['max_usd'], code='study_budget_invalid')
    require(0 < amount <= 2_000_000, 'study_budget_invalid')
    return value


def policy_task(task):
    """The legacy neutral task validator is reused, never its gold-reading batch."""
    full = set(task['initial_evidence_files']) == set(task['authorized_files'])
    return {key: value for key, value in task.items() if key not in {'project_id', 'license'}} | {
        'stratum': 'full_context' if full else 'retrieval_required',
        'evidence_parity': 'equal' if full else 'unequal_by_design'}


def _inventory(root, allowed):
    parents = {str(Path(name).parent) for name in allowed}
    parents |= {str(parent) for name in allowed for parent in Path(name).parents if str(parent) != '.'}
    seen = set()
    def scan(folder, prefix=''):
        with os.scandir(folder) as entries:
            for entry in entries:
                name = prefix + entry.name
                require(len(seen) < 128 and not entry.is_symlink(), 'pack_inventory_invalid')
                seen.add(name)
                if entry.is_dir(follow_symlinks=False):
                    require(name in parents, 'pack_inventory_invalid')
                    scan(Path(entry.path), name + '/')
                else:
                    require(entry.is_file(follow_symlinks=False) and name in allowed,
                            'pack_inventory_invalid')
    scan(root)
    require(allowed <= seen, 'pack_inventory_invalid')


def load_neutral_pack(pack_root):
    root = _root(pack_root)
    pack = read_json(root, 'pack.json', 65536)
    require(type(pack) is dict and set(pack) == {'schema', 'pack_id', 'tasks'}
        and pack['schema'] == PACK_SCHEMA and type(pack['pack_id']) is str
        and _ID.fullmatch(pack['pack_id']), 'pack_invalid')
    tasks = pack['tasks']
    require(type(tasks) is list and 1 <= len(tasks) <= 9, 'pack_task_count_invalid')
    ids, roots, allowed = set(), set(), {'pack.json'}
    for task in tasks:
        require(type(task) is dict and set(task) == TASK_FIELDS, 'neutral_task_fields_invalid')
        require(type(task['id']) is str and _ID.fullmatch(task['id'])
            and task['id'] not in ids, 'neutral_task_id_invalid')
        require(type(task['project_id']) is str and _ID.fullmatch(task['project_id']),
                'neutral_project_id_invalid')
        ids.add(task['id'])
        require(task['snapshot_root'] not in roots, 'duplicate_snapshot_root')
        roots.add(task['snapshot_root'])
        license = task['license']
        require(type(license) is dict and set(license) == {'spdx', 'provenance'}
            and all(type(v) is str and 0 < len(v.strip()) <= 1000 for v in license.values()),
            'license_declaration_required')
        try:
            _task(policy_task(task))
        except ValueError:
            raise StudyError('neutral_task_invalid') from None
        require(2 <= len(task['authorized_files']) <= 4
            and all(name.endswith('.py') for name in task['authorized_files']), 'source_count_invalid')
        allowed.update(task['snapshot_root'] + '/' + name for name in task['authorized_files'])
    # Validate names before opening any source. Extra gold/answers are rejected by name only.
    _inventory(root, allowed)
    for task in tasks:
        sizes = {name: len(_read(root, task['snapshot_root'] + '/' + name, 12289))
                 for name in task['authorized_files']}
        require(sum(sizes.values()) <= 12288, 'authorized_source_limit')
        require(sum(sizes[name] for name in task['initial_evidence_files']) <= 4096,
                'initial_source_limit')
    return pack


def _custodian(value):
    require(type(value) is dict and set(value) == {'schema', 'version', 'gold_sha256',
        'rubric_sha256', 'qualification_statement'} and
        value['schema'] == 'macr.deepseek.study.custodian.v1', 'custodian_commitment_invalid')
    for key in ('gold_sha256', 'rubric_sha256'):
        require(type(value[key]) is str and _HASH.fullmatch(value[key]), 'custodian_commitment_invalid')
    require(all(type(value[k]) is str and 0 < len(value[k].strip()) <= 2000
                for k in ('version', 'qualification_statement')), 'custodian_commitment_invalid')
    return value


def implementation(project_root):
    root = _root(project_root)
    require(root == Path(__file__).resolve().parents[3], 'implementation_root_mismatch')
    return {file: sha256(_read(root, file, 262144)).hexdigest()
            for file in sorted(set(IMPLEMENTATION_FILES) | set(IMPORTED_IMPLEMENTATION_FILES))}


def _execution_settings(profile):
    try:
        generation = get_generation(profile)
    except ValueError:
        raise StudyError('study_generation_profile_invalid') from None
    cell_budget = dict(DEFAULT_BUDGET) | {
        'max_total_output_tokens':generation['max_total_output_tokens']}
    price = {'checked_date':'2026-10-29', 'status':'planning_only_recheck_before_live',
        'reservation_micro_usd':generation['reservation_micro_usd'],
        'input_miss_per_million':'0.30', 'input_hit_per_million':'0.006',
        'output_per_million':'1.20', 'context_tokens':1000000}
    if profile is not None:
        del price['context_tokens']
        price.update(contract_input_envelope=generation['context_input_envelope'],
            context_exact_provider_limit_verified=False,
            minimum_reservation_micro_usd=generation['minimum_reservation_micro_usd'])
    limits = cell_budget | {'max_requests':3,
        'max_output_per_request':generation['max_output_per_request'],
        'planned_input_per_request':generation['planned_input_per_request'],
        'input_hard_cap_enforced':False}
    return generation, cell_budget, price, limits


def prepare_study(pack_root, *, custodian, out, project_root, budget=None, seed=20261029,
                  execution_profile=None):
    """Freeze neutral inputs and external commitments, without opening answer files.

    This prepares a proposed study, never a live authorization. A custodian's
    declaration is a claim to verify externally, not self-certified eligibility.
    """
    generation, cell_budget, price, limits = _execution_settings(execution_profile)
    pack = load_neutral_pack(pack_root)
    _custodian(custodian)
    budget = validate_budget(budget)
    require(type(seed) is int and 0 <= seed < 2**32, 'order_seed_invalid')
    code = implementation(project_root)
    output = Path(out).absolute()
    validate_workspace_path(output, output=True)
    require(not output.is_relative_to(Path(pack_root).absolute()), 'study_output_scope_invalid')
    require(not output.exists(), 'study_output_exists')
    output.mkdir(mode=0o700)
    (output / 'manifests').mkdir(mode=0o700)
    tasks = []
    for task in pack['tasks']:
        manifest = prepare_policy_manifest(policy_task(task), dataset_root=pack_root,
            out=output / 'manifests' / task['id'], model={'model_id':'deepseek-flash',
                'provider':'deepseek', 'thinking':True, 'reasoning_effort':'high'},
            budget=cell_budget,prompt_version=generation['prompt_version'])
        tasks.append(task | {'manifest':manifest,
                            'fixed_plan':build_fixed_plan(manifest, Path(pack_root))})
    projects = {}
    randomizer = random.Random(seed)
    for task in tasks:
        projects.setdefault(task['project_id'], []).append(task)
    groups = [projects[k] for k in sorted(projects)]
    for group in groups:
        randomizer.shuffle(group)
    order = [group[i] for i in range(max(map(len, groups))) for group in groups if i < len(group)]
    base = list(STRATEGIES)
    randomizer.shuffle(base)
    cells = []
    for index, task in enumerate(order):
        for strategy in base[index % 4:] + base[:index % 4]:
            cells.append({'task_id':task['id'], 'project_id':task['project_id'], 'strategy':strategy,
                'cell_id':digest({'pack':digest(pack), 'task':task['id'], 'strategy':strategy,
                    'prompt':generation['prompt_version'], 'code':code, 'seed':seed})[:32]})
    value = {'schema':SCHEMA, 'pack_id':pack['pack_id'], 'pack_sha256':digest(pack),
        'pack_root':str(Path(pack_root).absolute()), 'project_root':str(Path(project_root).absolute()),
        'control_root':str(output),
        'prompt_version':generation['prompt_version'], 'implementation':code, 'tasks':tasks, 'cells':cells,
        'custodian':custodian, 'budget':budget, 'order_seed':seed,
        'planned_request_ceiling':len(cells)*3, 'remote_enabled':False,
        'price_snapshot':price, 'cell_limits':limits}
    if execution_profile is not None:
        value['execution_profile'] = execution_profile
    value['profile_sha256'] = digest(value)
    _write_exclusive(output / 'study.json', value, 262144)
    return value


def load_study(out):
    root = _root(out)
    value = read_json(root, 'study.json')
    require(value.get('control_root') == str(root), 'control_path_mismatch')
    require(type(value) is dict and value.get('schema') == SCHEMA and
        value.get('remote_enabled') is False and value.get('profile_sha256') ==
        digest({k:v for k,v in value.items() if k != 'profile_sha256'}), 'study_profile_invalid')
    validate_budget(value['budget'])
    generation, cell_budget, price, limits = _execution_settings(value.get('execution_profile'))
    if 'execution_profile' in value:
        require(value['execution_profile']==V2_PROFILE,
            'study_generation_contract_drift')
    require(value['prompt_version']==generation['prompt_version']
        and value['cell_limits']==limits and value['price_snapshot']==price,
        'study_generation_contract_drift')
    require(implementation(value['project_root']) == value['implementation'], 'study_implementation_drift')
    pack = load_neutral_pack(value['pack_root'])
    require(digest(pack) == value['pack_sha256'], 'study_pack_drift')
    try:
        for task in value['tasks']:
            manifest = load_policy_manifest(root / 'manifests' / task['id'], dataset_root=value['pack_root'])
            require(manifest == task['manifest'], 'study_manifest_drift')
            require(manifest['budget']==cell_budget and manifest['prompt_version']==generation['prompt_version'],
                'study_generation_contract_drift')
            plan = build_fixed_plan(manifest, Path(value['pack_root']))
            require(plan['sha256'] == task['fixed_plan']['sha256'], 'study_fixed_plan_drift')
    except ValueError:
        raise StudyError('study_manifest_or_source_drift') from None
    return value
