import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import IndexQuery, ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore, SnapshotError
from macr.investigation.validation import ContractError

try:
    from macr.investigation.static_facts import collect_static_facts
except ModuleNotFoundError:
    collect_static_facts = None


class StaticFactsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(collect_static_facts, 'bounded static fact collector missing')

    def package(self, files, seeds=('start',), budget=1200):
        root = write_files(fixture_root(), files)
        store = SnapshotStore(fixture_root() / 'snapshots')
        bundle = store.capture(root, RepositoryScope().inventory(root, ScopeProfile()))
        index = RepositoryIndex(store, fixture_root())
        index.build(bundle)
        symbols = index.query(bundle.ref, IndexQuery(limit=50)).symbols
        seed_ids = [s.symbol_id for s in symbols if s.qualified_name in seeds and s.kind == 'function']
        forbidden = AssertionError('facts must not execute analysed source or contact a service')
        with patch('subprocess.Popen', side_effect=forbidden), patch('socket.create_connection', side_effect=forbidden), \
             patch('urllib.request.build_opener', side_effect=forbidden):
            package = collect_static_facts(bundle, index, seed_ids, max_context_bytes=budget)
        return package, bundle, index, seed_ids

    def test_cross_file_alias_returns_are_source_facts_not_runtime_flow(self):
        package, bundle, _, _ = self.package({
            'values.py': "def measure():\n    return 2.5\n",
            'target.py': "def consume(value):\n    return value\n",
            'entry.py': "from values import measure as obtain\nfrom target import consume as deliver\ndef start():\n    amount = obtain()\n    return deliver(amount)\n",
        })
        self.assertEqual({s['file'] for s in package['symbols']}, {'values.py', 'target.py', 'entry.py'})
        returns = [f for f in package['facts'] if f['kind'] == 'return_type' and f['location']['file'] == 'values.py']
        self.assertEqual([f['type'] for f in returns], ['float'])
        binding = next(b for b in package['bindings'] if b['callee_name'] == 'consume')
        self.assertEqual(binding['arguments'][0]['parameter'], 'value')
        self.assertEqual(binding['arguments'][0]['type'], 'unknown')
        self.assertFalse(package['data_flow_proven'])
        self.assertFalse(package['repository_code_executed'])
        self.assertTrue(package['assumptions'])
        evidence = {e['evidence_id']: e for e in package['evidence']}
        for record in package['facts'] + package['bindings']:
            self.assertTrue(record['evidence_ids'])
            self.assertTrue(set(record['evidence_ids']) <= set(evidence))
            self.assertTrue(all(evidence[e]['snapshot_id'] == bundle.ref.snapshot_id for e in record['evidence_ids']))

    def test_simple_assignment_keyword_binding_distinguishes_bool_from_int(self):
        package, _, _, _ = self.package({'entry.py':
            "def combine(left, *, right):\n    return (left, right)\ndef start():\n    amount = 2.5\n    return combine(amount, right=True)\n"})
        binding = next(b for b in package['bindings'] if b['callee_name'] == 'combine')
        self.assertEqual([(a['parameter'], a['type']) for a in binding['arguments']], [('left', 'float'), ('right', 'bool')])
        self.assertEqual(binding['status'], 'syntactic_binding')
        self.assertTrue(any(f['kind'] == 'assignment_type' and f['name'] == 'amount' and f['type'] == 'float' for f in package['facts']))

    def test_conversion_result_is_unknown_not_the_input_string_type(self):
        package, _, _, _ = self.package({'entry.py':
            "def consume(value):\n    return value\ndef start():\n    converted = int('6')\n    return consume(converted)\n"})
        binding = next(b for b in package['bindings'] if b['callee_name'] == 'consume')
        self.assertEqual(binding['arguments'][0]['type'], 'unknown')
        self.assertTrue(any(u['reason'] == 'call_result_unknown' for u in package['unknowns']))
        self.assertNotIn('bug', package)

    def test_branch_returns_are_unknown_even_when_each_literal_is_known(self):
        package, _, _, _ = self.package({'entry.py':
            "def choose(flag):\n    if flag:\n        return 4\n    return 'four'\ndef start():\n    return choose(True)\n"})
        returns = [f for f in package['facts'] if f['kind'] == 'return_type' and f['name'] == 'choose']
        self.assertEqual([f['type'] for f in returns], ['unknown'])
        self.assertTrue(any(u['reason'] == 'control_flow_unsupported' for u in package['unknowns']))

    def test_annotations_are_declarations_and_do_not_override_literal_type(self):
        package, _, _, _ = self.package({'entry.py': "def start():\n    label: int = 'word'\n    return label\n"})
        self.assertTrue(any(f['kind'] == 'annotation' and f['annotation'] == 'int' for f in package['facts']))
        self.assertEqual([f['type'] for f in package['facts'] if f['kind'] == 'return_type'], ['str'])

    def test_parameter_shadowing_does_not_follow_same_named_global(self):
        package, _, _, _ = self.package({'entry.py': "def loader():\n    return 8\ndef start(loader):\n    return loader()\n"})
        self.assertEqual([s['qualified_name'] for s in package['symbols']], ['start'])
        self.assertEqual(package['bindings'], [])
        self.assertTrue(any(u['reason'] == 'relation_unresolved' for u in package['unknowns']))

    def test_decorated_target_remains_unknown(self):
        package, _, _, _ = self.package({'entry.py':
            "def decorate(f):\n    return f\n@decorate\ndef value():\n    return 7\ndef start():\n    return value()\n"})
        self.assertEqual(package['bindings'], [])
        self.assertTrue(any(u['reason'] == 'relation_syntactic_candidate' for u in package['unknowns']))

    def test_duplicate_import_alias_does_not_become_a_known_binding(self):
        package, _, _, _ = self.package({
            'first.py': "def value():\n    return 1\n",
            'second.py': "def value():\n    return 'other'\n",
            'entry.py': "from first import value as load\nfrom second import value as load\ndef start():\n    return load()\n",
        })
        self.assertEqual(package['bindings'], [])
        self.assertTrue(any(u['reason'] == 'alias_or_module_binding_ambiguous' for u in package['unknowns']))

    def test_nested_calls_on_one_line_are_unknown_not_arbitrarily_paired(self):
        package, _, _, _ = self.package({'entry.py':
            "def source():\n    return 2\ndef consume(value):\n    return value\ndef start():\n    return consume(source())\n"})
        self.assertEqual(package['bindings'], [])
        self.assertTrue(any(u['reason'] == 'call_location_ambiguous' for u in package['unknowns']))

    def test_defaults_and_star_arguments_are_outside_supported_signature(self):
        for source in [
            "def take(value=1):\n    return value\ndef start():\n    return take()\n",
            "def take(value):\n    return value\ndef start():\n    args = (1,)\n    return take(*args)\n",
        ]:
            with self.subTest(source=source):
                package, _, _, _ = self.package({'entry.py': source})
                self.assertEqual(package['bindings'], [])
                self.assertTrue(any(u['reason'] in ('signature_unsupported', 'argument_expansion_unsupported') for u in package['unknowns']))

    def test_expansion_is_one_hop_and_at_most_six_functions(self):
        package, _, _, _ = self.package({'entry.py':
            "def remote():\n    return 1\ndef middle():\n    return remote()\ndef start():\n    return middle()\n"})
        self.assertEqual({s['qualified_name'] for s in package['symbols']}, {'start', 'middle'})
        self.assertTrue(any(u['reason'] == 'outside_one_hop' for u in package['unknowns']))
        defs = ''.join(f'def item{i}():\n    return {i}\n' for i in range(7))
        calls = ''.join(f'    item{i}()\n' for i in range(7))
        capped, _, _, _ = self.package({'entry.py': defs + 'def start():\n' + calls + '    return None\n'})
        self.assertEqual(len(capped['symbols']), 6)
        self.assertTrue(any(u['reason'] == 'function_limit' for u in capped['unknowns']))

    def test_truncation_publishes_no_partial_facts_or_bindings(self):
        package, _, _, _ = self.package({'entry.py': "def start():\n    value = 7\n    return value\n"}, budget=8)
        self.assertEqual(package['facts'], [])
        self.assertEqual(package['bindings'], [])
        self.assertTrue(package['truncations'])
        self.assertTrue(any(u['reason'] == 'context_truncated' for u in package['unknowns']))
        self.assertLessEqual(package['source_bytes'], 8)

    def test_snapshot_drift_is_rejected_before_facts_are_collected(self):
        _, bundle, index, seeds = self.package({'entry.py': "def start():\n    return 7\n"})
        (Path(bundle.ref.root_ref) / 'entry.py').write_text("def start():\n    return 8\n")
        with self.assertRaises(SnapshotError):
            collect_static_facts(bundle, index, seeds)

    def test_rejects_invalid_seed_and_budget_without_opening_unapproved_paths(self):
        _, bundle, index, seeds = self.package({'entry.py': "def start():\n    return 7\n"})
        for ids, budget in [([], 1200), (seeds * 7, 1200), (['../.env'], 1200), (seeds, True)]:
            with self.subTest(ids=ids, budget=budget), self.assertRaises(ContractError):
                collect_static_facts(bundle, index, ids, max_context_bytes=budget)

    def test_function_local_imports_are_unknown_not_a_unique_global_binding(self):
        package, _, _, _ = self.package({
            'first.py': "def receive(value):\n    return value\n",
            'second.py': "def receive(value):\n    return value\n",
            'entry.py': "from first import receive\ndef start():\n    from first import receive\n    from second import receive\n    return receive(7)\n",
        })
        self.assertEqual(package['bindings'], [])
        self.assertTrue(any(u['reason'] == 'control_flow_unsupported' for u in package['unknowns']))

    def test_drift_during_truncated_read_is_still_rejected(self):
        _, bundle, index, seeds = self.package({'entry.py': "def start():\n    return 7\n"})
        from macr.investigation.context import ContextReader
        original = ContextReader.read
        def read_then_mutate(reader, ref, request):
            result = original(reader, ref, request)
            (Path(ref.root_ref) / 'entry.py').write_text("def start():\n    return 8\n")
            return result
        with patch.object(ContextReader, 'read', read_then_mutate), self.assertRaises(SnapshotError):
            collect_static_facts(bundle, index, seeds, max_context_bytes=8)

    def test_partial_relation_page_does_not_publish_argument_bindings(self):
        calls = '    receive(1)\n' * 51
        package, _, _, _ = self.package({'entry.py':
            'def receive(value):\n    return value\ndef start():\n' + calls + '    return None\n'})
        self.assertFalse(package['truncations'])
        self.assertTrue(any(u['reason'] == 'relation_query_limit' for u in package['unknowns']))
        self.assertEqual(package['bindings'], [])

    def test_exception_group_control_flow_does_not_publish_straight_line_bindings(self):
        package, _, _, _ = self.package({'entry.py':
            "def receive(value):\n    return value\ndef start():\n    try:\n        receive(1)\n    except* Exception:\n        receive(2)\n    return 3\n"})
        self.assertEqual(package['bindings'], [])
        self.assertEqual([f['type'] for f in package['facts'] if f['kind'] == 'return_type' and f['name'] == 'start'], ['unknown'])
        self.assertTrue(any(u['reason'] == 'control_flow_unsupported' for u in package['unknowns']))

    def test_conditional_expressions_do_not_publish_unconditional_bindings(self):
        for expression in ('receive(1) if flag else receive(2)', 'flag and receive(1)'):
            with self.subTest(expression=expression):
                package, _, _, _ = self.package({'entry.py':
                    'def receive(value):\n    return value\ndef start(flag):\n    return ' + expression + '\n'})
                self.assertEqual(package['bindings'], [])
                self.assertTrue(any(u['reason'] == 'control_flow_unsupported' for u in package['unknowns']))
