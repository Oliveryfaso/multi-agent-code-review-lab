"""New preview-cache fixtures only; repository strings are never executed."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import FileEntry, ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError

try:
    from macr.investigation import repair_localize as localize
except ImportError:
    localize = None


MESSAGE = 'preview cache entry expired'
CACHE = 'def fetch_record(key):\n    raise ValueError("preview cache entry expired")\n'
RENDER = 'from cache import fetch_record\n\ndef render_page(key):\n    return fetch_record(key)\n'


class RepairLocalizeTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(localize, 'snapshot-bound lexical locator is missing')

    def fixture(self, files, *, unreadable=False):
        root = fixture_root()
        source = write_files(root / 'repo', files)
        scope = RepositoryScope().inventory(source, ScopeProfile(max_file_bytes=512*1024, max_text_bytes=2*1024*1024))
        if unreadable: scope.unreadable.append(FileEntry('unreadable.py', reason='unreadable_file'))
        store = SnapshotStore(root / 'snapshots', allowed_root=root)
        bundle = store.capture(source, scope)
        index = RepositoryIndex(store, root / 'index')
        index.build(bundle)
        return bundle, index

    def propose(self, files, issue=None, **kwargs):
        bundle, index = self.fixture(files)
        return localize.propose_repair_sources(bundle, index, f'The operation raises "{MESSAGE}".' if issue is None else issue,
                                              **kwargs)

    def test_exact_error_literal_leads_to_cache_and_one_resolved_caller_not_comments(self):
        files = {'cache.py': CACHE, 'render.py': RENDER,
                 'archive.py': ('# ' + MESSAGE + '\n') * 40 + 'def archive(): pass\n',
                 'top.py': 'from render import render_page\ndef serve(key): return render_page(key)\n'}
        result = self.propose(files)
        self.assertEqual(result['status'], 'candidates_proposed')
        self.assertEqual(result['candidates'][0]['path'], 'cache.py')
        self.assertNotIn('archive.py', [row['path'] for row in result['candidates']])
        self.assertNotIn('top.py', [row['path'] for row in result['candidates']])
        render = next(row for row in result['candidates'] if row['path'] == 'render.py')
        self.assertTrue(any(e['kind'] == 'static_call' for e in render['evidence']))
        cache = result['candidates'][0]
        self.assertTrue(any(e['kind'] == 'exact_phrase' and e['term'] == MESSAGE for e in cache['evidence']))
        for row in result['candidates']:
            body = files[row['path']].encode()
            self.assertEqual((row['bytes'], row['sha256']), (len(body), sha256(body).hexdigest()))
        self.assertEqual(result['source_files'], {row['path']: row['sha256'] for row in result['candidates']})
        self.assertFalse(result['task_ready']); self.assertEqual(result['model_calls'], 0)
        self.assertFalse(result['target_code_executed'])
        self.assertNotIn('editable_paths', result); self.assertNotIn('test_paths', result)

    def test_camel_snake_and_signature_terms_need_no_case_specific_mapping(self):
        result = self.propose({'session.py': 'def rotateRefreshToken(preview_entry):\n    return preview_entry\n'},
                              'The refresh token uses the preview entry.')
        self.assertEqual(result['candidates'][0]['path'], 'session.py')
        terms = {e['term'] for e in result['candidates'][0]['evidence'] if 'term' in e}
        self.assertTrue({'refresh', 'token', 'preview', 'entry'} <= terms)
        self.assertTrue(any(e['kind'] == 'signature' for e in result['candidates'][0]['evidence']))

    def test_body_evidence_points_to_the_word_inside_multiline_strings(self):
        text='def observe():\n    note = """first line\n    Straße timedExpiry appears\n    """\n    return note\n'
        result=self.propose({'sample.py':text},'timed expiry')
        proof=next(e for e in result['candidates'][0]['evidence'] if e['kind']=='body' and e['term']=='expiry')
        self.assertEqual(proof['line'],3)
        self.assertEqual(proof['column'],text.splitlines()[2].index('Expiry')+1)

    def test_signature_evidence_points_to_multiline_parameter_word(self):
        text='def observe(\n    stale_cache,\n):\n    return stale_cache\n'
        result=self.propose({'sample.py':text},'stale cache')
        proofs={e['term']:e for e in result['candidates'][0]['evidence'] if e['kind']=='signature'}
        for term in ('stale','cache'):
            self.assertEqual(proofs[term]['line'],2)
            self.assertEqual(proofs[term]['column'],text.splitlines()[1].index(term)+1)

    def test_path_evidence_keeps_original_unicode_offsets(self):
        path='straßeCache.py'
        result=self.propose({path:'def ordinary(): pass\n'},'cache')
        proof=next(e for e in result['candidates'][0]['evidence'] if e['kind']=='path')
        self.assertEqual(proof['column'],path.index('Cache')+1)

    def test_source_positions_use_python_logical_lines_without_rewriting_bytes(self):
        text='value=1\rmessage="""header\rrareMarker"""\r'
        result=self.propose({'sample.py':text},'rare marker')
        proof=next(e for e in result['candidates'][0]['evidence'] if e['kind']=='body' and e['term']=='marker')
        self.assertEqual(proof['line'],3)
        self.assertEqual(proof['column'],text.splitlines()[2].index('Marker')+1)
        self.assertEqual(result['candidates'][0]['sha256'],sha256(text.encode()).hexdigest())

    def test_no_lexical_evidence_has_no_alphabetical_fallback(self):
        result = self.propose({'a.py': 'def ordinary(value): return value\n'}, 'quantum banana harmonics')
        self.assertEqual((result['status'], result['candidates'], result['source_files']), ('inconclusive', [], None))

    def test_repeated_body_words_do_not_inflate_score(self):
        single = self.propose({'cache.py': 'def fetch():\n    return "expired"\n'}, 'expired')
        repeated = self.propose({'cache.py': 'def fetch():\n' + '    value = "expired"\n' * 30 + '    return value\n'}, 'expired')
        self.assertEqual(single['candidates'][0]['score'], repeated['candidates'][0]['score'])

    def test_explicit_repository_path_beats_generic_terms_and_quoted_noise(self):
        body = 'def ordinary(): return 1\n'
        result = self.propose({'pkg/target.py': body,
            'noise.py': 'def preview_cache_entry(): return "entry expired"\n'},
            'Inspect `pkg/target.py`. The preview cache reports "entry expired".')
        self.assertEqual(result['candidates'][0]['path'], 'pkg/target.py')
        proof = next(e for e in result['candidates'][0]['evidence'] if e['kind'] == 'explicit_path')
        self.assertEqual(proof['resolution'], 'repository_relative')
        self.assertEqual((proof['file'], proof['source_sha256'], proof['source_bytes']),
                         ('pkg/target.py', sha256(body.encode()).hexdigest(), len(body.encode())))

    def test_unique_bare_basename_resolves_only_inside_snapshot(self):
        result = self.propose({'pkg/target.py': 'def ordinary(): pass\n',
            'noise.py': 'def preview_cache(): return "expired"\n'},
            'target.py reports preview cache "expired"')
        self.assertEqual(result['candidates'][0]['path'], 'pkg/target.py')
        proof = next(e for e in result['candidates'][0]['evidence'] if e['kind'] == 'explicit_path')
        self.assertEqual(proof['resolution'], 'unique_basename')

    def test_ambiguous_basename_has_no_anchor_priority_and_reports_gap(self):
        files = {'a/target.py': 'def ordinary(): pass\n', 'b/target.py': 'def ordinary(): pass\n',
                 'noise.py': 'def preview_cache(): return "expired"\n'}
        result = self.propose(files, 'target.py reports preview cache "expired"')
        self.assertEqual(result['candidates'][0]['path'], 'noise.py')
        self.assertFalse(any(e['kind'] == 'explicit_path' for row in result['candidates'] for e in row['evidence']))
        gap = next(g for g in result['gaps'] if g['code'] == 'ambiguous_path_basename')
        self.assertEqual((gap['term'], gap['count'], gap['paths']), ('target.py', 2, ['a/target.py', 'b/target.py']))

    def test_unknown_qualified_path_never_falls_back_to_unique_basename(self):
        files = {'pkg/target.py': 'def ordinary(): pass\n',
                 'noise.py': 'def preview_cache(): return "expired"\n'}
        for mention in ('other/target.py', r'other\target.py', r'C:\other\target.py'):
            with self.subTest(mention=mention):
                result = self.propose(files, mention + ' preview cache "expired"')
                self.assertEqual(result['candidates'][0]['path'], 'noise.py')
                self.assertFalse(any(e['kind'] == 'explicit_path' for row in result['candidates'] for e in row['evidence']))

    def test_windows_traceback_suffix_preserves_original_issue_span(self):
        mention = r'C:\work\pkg\target.py'
        issue = 'Straße preview\n  File "' + mention + '", line 7, in ordinary\n'
        result = self.propose({'pkg/target.py': 'def ordinary(): pass\n'}, issue)
        proof = next(e for e in result['candidates'][0]['evidence'] if e['kind'] == 'explicit_path')
        self.assertEqual(proof['resolution'], 'windows_suffix')
        self.assertEqual((proof['line'], proof['column'], proof['position_basis']), (2, 9, 'issue'))
        self.assertEqual(proof['issue_span'], {'start': 23, 'end': 44})
        self.assertEqual(issue[23:44], mention)
        self.assertEqual(proof['term'], mention)
        self.assertEqual(proof['issue_sha256'], sha256(issue.encode()).hexdigest())

    def test_issue_path_positions_handle_crlf_and_lone_cr_without_rewriting_spans(self):
        for newline, start, end in (('\r\n', 10, 23), ('\r', 9, 22)):
            with self.subTest(newline=repr(newline)):
                issue = 'notice' + newline + '  pkg/target.py'
                result = self.propose({'pkg/target.py': 'def ordinary(): pass\n'}, issue)
                proof = next(e for e in result['candidates'][0]['evidence'] if e['kind'] == 'explicit_path')
                self.assertEqual((proof['line'], proof['column']), (2, 3))
                self.assertEqual(proof['issue_span'], {'start': start, 'end': end})
                self.assertEqual(issue[start:end], proof['term'])
                self.assertEqual(proof['issue_sha256'], sha256(issue.encode()).hexdigest())

    def test_windows_suffix_ambiguity_does_not_choose_one_matching_path(self):
        result = self.propose({'pkg/target.py': 'def ordinary(): pass\n',
            'repo/pkg/target.py': 'def ordinary(): pass\n',
            'noise.py': 'def preview_cache(): return "expired"\n'},
            r'C:\repo\pkg\target.py preview cache "expired"')
        self.assertEqual(result['candidates'][0]['path'], 'noise.py')
        self.assertFalse(any(e['kind'] == 'explicit_path' for row in result['candidates'] for e in row['evidence']))
        gap = next(g for g in result['gaps'] if g['code'] == 'ambiguous_path_suffix')
        self.assertEqual((gap['count'], gap['paths']), (2, ['pkg/target.py', 'repo/pkg/target.py']))

    def test_invalid_path_tokens_do_not_anchor_or_change_lexical_ranking(self):
        files = {'pkg/target.py': 'def ordinary(): pass\n',
                 'noise.py': 'def preview_cache(): return "expired"\n'}
        for mention in ('target.pyc', 'target.pyx', 'target.py.bak', '../pkg/target.py',
                        'pkg/../target.py', r'..\pkg\target.py', 'pkg/Target.py', 'x-pkg/target.py'):
            with self.subTest(mention=mention):
                result = self.propose(files, mention + ' preview cache "expired"')
                self.assertEqual(result['candidates'][0]['path'], 'noise.py')
                self.assertFalse(any(e['kind'] == 'explicit_path' for row in result['candidates'] for e in row['evidence']))

    def test_path_repetitions_and_alternate_spellings_do_not_inflate_score(self):
        files = {'pkg/target.py': 'def ordinary(): pass\n'}
        single = self.propose(files, 'pkg/target.py')
        repeated = self.propose(files, 'pkg/target.py target.py ' + r'pkg\target.py ' * 10)
        self.assertEqual(single['candidates'][0]['score'], repeated['candidates'][0]['score'])
        self.assertEqual(sum(e['kind'] == 'explicit_path' for e in repeated['candidates'][0]['evidence']), 1)

    def test_out_of_scope_path_mentions_never_expand_snapshot_reads(self):
        bundle, index = self.fixture({'pkg/target.py': 'def ordinary(): pass\n',
            'secrets/private.py': 'raise RuntimeError("must never read")\n'})
        read = index.store.read_verified
        def bounded_read(ref, path):
            if path not in bundle.files: raise AssertionError('issue expanded snapshot reads')
            return read(ref, path)
        with patch.object(index.store, 'read_verified', side_effect=bounded_read):
            result = localize.propose_repair_sources(bundle, index,
                'pkg/target.py secrets/private.py ../../outside.py')
        self.assertEqual(set(result['source_files']), {'pkg/target.py'})
        proofs = [e for row in result['candidates'] for e in row['evidence'] if e['kind'] == 'explicit_path']
        self.assertEqual([e['file'] for e in proofs], ['pkg/target.py'])

    def test_tied_cutoff_is_explicit_and_cannot_offer_arbitrary_source_set(self):
        result = self.propose({'a.py': 'def fetch(): return "expired"\n',
                               'b.py': 'def fetch(): return "expired"\n'}, 'expired', max_candidates=1)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertIsNone(result['source_files'])
        self.assertTrue(any(gap['code'] == 'candidate_tie_at_limit' for gap in result['gaps']))

    def test_unresolved_calls_are_not_expanded_and_hops_do_not_iterate(self):
        result = self.propose({'cache.py': CACHE, 'render.py': RENDER,
            'top.py': 'from render import render_page\ndef serve(key): return render_page(key)\n',
            'unresolved.py': 'def run(loader):\n    value = "entry expired"\n    return loader(value)\n',
            'other.py': 'def loader(value): return value\n'}, '"entry expired"')
        paths = {row['path'] for row in result['candidates']}
        self.assertIn('render.py', paths); self.assertNotIn('top.py', paths); self.assertNotIn('other.py', paths)
        self.assertTrue(any(gap['code'] == 'unresolved_calls_not_expanded' for gap in result['gaps']))

    def test_explicit_public_test_name_can_propose_its_resolved_implementation(self):
        result = self.propose({'tests/test_preview.py':
            'from engine import fetch_record\ndef test_preview_expiry():\n    assert fetch_record(1) == 1\n',
            'engine.py': 'def fetch_record(key): return key\n'}, 'test_preview_expiry')
        implementation = next(row for row in result['candidates'] if row['path'] == 'engine.py')
        self.assertTrue(any(e['kind'] == 'static_call' and e['relation_type'] == 'tests'
                            for e in implementation['evidence']))
        self.assertNotIn('editable_paths', result)

    def test_large_selected_file_and_total_budget_are_reported_without_replacement(self):
        large = CACHE + '#' + 'x' * 17000 + '\n'
        result = self.propose({'cache.py': large, 'small.py': 'def entry(): return "expired"\n'})
        self.assertEqual(result['status'], 'source_budget_exceeded')
        self.assertIsNone(result['source_files'])
        self.assertIn('cache.py', [row['path'] for row in result['candidates']])
        base = 'def fetch(): return "expired"\n'
        full = base + '#' + 'x' * (16384 - len(base.encode()) - 2) + '\n'
        result = self.propose({f'unit{i}.py': full for i in range(5)}, 'expired')
        self.assertEqual((result['status'], len(result['candidates'])), ('source_budget_exceeded', 5))
        self.assertIsNone(result['source_files'])

    def test_parse_or_unreadable_coverage_gaps_keep_evidence_but_not_source_selection(self):
        for files, unreadable in (({'cache.py': CACHE, 'broken.py': 'def broken('}, False),
                                  ({'cache.py': CACHE}, True)):
            with self.subTest(unreadable=unreadable):
                bundle, index = self.fixture(files, unreadable=unreadable)
                result = localize.propose_repair_sources(bundle, index, '"' + MESSAGE + '"')
                self.assertEqual(result['status'], 'inconclusive')
                self.assertIsNone(result['source_files']); self.assertTrue(result['candidates'])
                self.assertTrue(result['coverage']['parse_or_index_gaps'] or result['coverage']['unreadable'])

    def test_scope_exclusions_do_not_expand_from_issue_or_count_as_missing_source(self):
        result = self.propose({'cache.py': CACHE, 'secrets/private.py': 'raise RuntimeError("expired")\n'},
                              '"' + MESSAGE + '" at secrets/private.py')
        self.assertEqual(result['status'], 'candidates_proposed')
        self.assertNotIn('secrets/private.py', result['source_files'])
        self.assertGreater(result['coverage']['excluded_count'], 0)

    def test_snapshot_content_index_and_forged_bundle_are_checked(self):
        bundle, index = self.fixture({'cache.py': CACHE})
        forged = deepcopy(bundle); forged.files['cache.py'].content = b'forged'
        with self.assertRaises(ContractError): localize.propose_repair_sources(forged, index, MESSAGE)
        forged = deepcopy(bundle); forged.files['../unsafe.py'] = forged.files.pop('cache.py')
        with self.assertRaises(ContractError): localize.propose_repair_sources(forged, index, MESSAGE)
        original_id = index.content_id; index.content_id = '0' * 64
        with self.assertRaises(ContractError): localize.propose_repair_sources(bundle, index, MESSAGE)
        index.content_id = original_id
        (Path(bundle.ref.root_ref) / 'cache.py').write_bytes(b'value = 1\n')
        with self.assertRaises(ContractError): localize.propose_repair_sources(bundle, index, MESSAGE)

    def test_issue_and_candidate_limits_precede_reads_and_terms_are_bounded(self):
        bundle, index = self.fixture({'cache.py': CACHE})
        for issue in (None, '', '   ', 'x' * 2049, '\ud800', 'preview\x00cache'):
            with self.subTest(issue=repr(issue)), patch.object(index.store, 'verify') as verify:
                with self.assertRaises(ContractError): localize.propose_repair_sources(bundle, index, issue)
                verify.assert_not_called()
        for count in (0, True, 33):
            with self.subTest(count=count), self.assertRaises(ContractError):
                localize.propose_repair_sources(bundle, index, MESSAGE, max_candidates=count)
        issue = ' '.join(f'signal{i}' for i in range(40))
        result = localize.propose_repair_sources(bundle, index, issue)
        self.assertEqual(len(result['terms']), 24)
        self.assertEqual(result['issue_sha256'], sha256(issue.encode()).hexdigest())
        self.assertEqual(result['snapshot_content_id'], bundle.ref.content_id)
        self.assertTrue(any(gap['code'] == 'term_limit' for gap in result['gaps']))


if __name__ == '__main__': unittest.main()
