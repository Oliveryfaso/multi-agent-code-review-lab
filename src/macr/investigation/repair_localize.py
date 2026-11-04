"""Snapshot-bound lexical candidates and one syntactic call hop, never diagnosis.

No repository capture, target imports, model calls or execution occur here.
The proposed source set grants no edit/test authority or dependency completeness.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import io
import re
import tokenize

from .index import RepositoryIndex
from .records import CapturedFile, IndexQuery, SnapshotBundle
from .repair_edits import MAX_FILES, validate_files
from .repair_io import utf8_size
from .repair_sources import _commitments
from .validation import ContractError


STOP_WORDS = frozenset('a an and are as at be been being because before after but by can could do does '
    'for from had has have how i if in into is it its may my no not of on or our please should than '
    'that the their then there these they this those to use uses using was we were when where which '
    'while will with would you your bug error errors fail failed fails failure fix problem'.split())

# Consume the whole path-shaped mention so an unknown/traversing qualified
# path cannot be rescanned as a bare basename. A terminal sentence dot is OK.
PATH_MENTION = re.compile(r'(?<![\w./\\:-])(?:[A-Za-z]:[\\/]|[\\/]{1,2})?'
                          r'(?:[\w.-]+[\\/])*[\w.-]+\.py(?![\w/\\-]|\.[\w./\\-])')
PATH_ANCHOR_SCORE = 10_000_000  # Above the bounded 8-phrase/24-term lexical maximum.


def _require(ok, field):
    if not ok: raise ContractError('repair_localize_invalid', field)


def _word_spans(text):
    # Keep offsets in original text; casefold can change Unicode string length.
    for match in re.finditer(r'[^\W_]+',text):
        raw=match.group()
        cuts=[0]+[m.start() for m in re.finditer(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])',raw)]+[len(raw)]
        for start,end in zip(cuts,cuts[1:]):
            word=raw[start:end]
            if len(word)>1 and any(c.isalpha() for c in word) and word.casefold() not in STOP_WORDS:
                yield word.casefold(),match.start()+start


def _words(text):
    return [word for word,offset in _word_spans(text)]


def _path_mentions(issue):
    for match in PATH_MENTION.finditer(issue):
        normalized = match.group().replace('\\', '/')
        if any(part in ('.', '..') for part in normalized.split('/')): continue
        yield match, normalized


def _position(token,offset):
    prefix=token.string[:offset]
    return (token.start[0]+prefix.count('\n'),
            len(prefix.rsplit('\n',1)[-1])+1 if '\n' in prefix else token.start[1]+offset+1)


def _tokens(text):
    items = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text,newline=None).readline): items.append(token)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return items, False
    return items, True


def _header(tokens, symbol):
    items = []; depth = 0
    for token in tokens:
        if token.start[0] < symbol.start: continue
        if token.start[0] > symbol.end: break
        if token.type == tokenize.OP:
            if token.string == ':' and depth == 0: break
            if token.string in '([{': depth += 1
            if token.string in ')]}': depth -= 1
        items.append(token)
    return items


def propose_repair_sources(bundle, index, issue, *, max_candidates=8):
    """Propose full committed files from an already authorized snapshot and index."""
    _require(type(issue) is str and bool(issue.strip()) and '\x00' not in issue
             and 0 < utf8_size(issue) <= 2048, 'issue')
    _require(type(max_candidates) is int and 1 <= max_candidates <= MAX_FILES, 'max_candidates')
    _require(isinstance(bundle, SnapshotBundle) and isinstance(index, RepositoryIndex)
             and index.content_id == bundle.ref.content_id, 'snapshot_index_binding')
    issue_sha256 = sha256(issue.encode('utf-8')).hexdigest()
    all_terms = list(dict.fromkeys(_words(issue))); terms = all_terms[:24]
    all_phrases = list(dict.fromkeys(match.group(2) for match in re.finditer(r'([\"\'`])([^\r\n]*?)\1', issue)
                                   if match.group(2).strip()))
    phrases = all_phrases[:8]; gaps = []
    if len(all_terms) > len(terms): gaps.append({'code': 'term_limit', 'retained': len(terms), 'available': len(all_terms)})
    if len(all_phrases) > len(phrases): gaps.append({'code': 'phrase_limit', 'retained': len(phrases), 'available': len(all_phrases)})
    page = index.query(bundle.ref, IndexQuery(limit=max(1, len(index.symbols))))
    _require(page.next_cursor is None, 'index_symbol_page')
    manifest = index.store._manifest(bundle.ref)
    entries = {entry['path']: entry for entry in manifest.files}
    _require(set(bundle.files) == set(entries)
             and {entry.path for entry in bundle.scope.eligible} == set(entries), 'bundle_inventory_binding')
    for path, entry in entries.items():
        captured = bundle.files[path]
        _require(isinstance(captured, CapturedFile) and captured.entry.path == path
                 and type(captured.content) is bytes and len(captured.content) == entry['size_bytes']
                 and captured.digest == entry['sha256'] == sha256(captured.content).hexdigest(),
                 'bundle_file_binding')
    contents = {path: index.store.read_verified(bundle.ref, path) for path, entry in entries.items()
                if entry['language'] == 'python'}
    symbols = {symbol.symbol_id: symbol for symbol in page.symbols}
    _require(len(symbols) == len(page.symbols), 'index_symbol_binding')
    by_file = defaultdict(list)
    for symbol in symbols.values():
        _require(symbol.file in contents and 1 <= symbol.start <= symbol.end <= max(1, len(contents[symbol.file].splitlines()))
                 and symbol.symbol_id == f'{bundle.ref.content_id}:{symbol.file}:{symbol.qualified_name}:{symbol.start}',
                 'index_symbol_binding')
        by_file[symbol.file].append(symbol)
    missing_index = sorted(set(contents) - {symbol.file for symbol in symbols.values() if symbol.kind == 'file'})
    coverage = {'eligible_files': len(entries), 'python_files': len(contents),
        'parsed_files': len(contents) - len(missing_index), 'parse_or_index_gaps': missing_index,
        'unreadable': [{'file': entry.path, 'reason': entry.reason} for entry in bundle.scope.unreadable],
        'scope_errors': [{'code': error.code, 'detail': error.detail} for error in bundle.scope.errors],
        'excluded_count': len(bundle.scope.excluded), 'unsupported_count': len(bundle.scope.unsupported),
        'lexical_scan_errors': []}
    if missing_index: gaps.append({'code': 'parse_or_index_gaps', 'files': missing_index})
    if coverage['unreadable']: gaps.append({'code': 'unreadable_scope_files', 'count': len(coverage['unreadable'])})
    if coverage['scope_errors']: gaps.append({'code': 'scope_errors', 'count': len(coverage['scope_errors'])})
    candidates = {}; seed_ids = set()

    def candidate(path):
        if path not in candidates:
            candidates[path] = {'path': path, 'sha256': sha256(contents[path]).hexdigest(), 'bytes': len(contents[path]),
                'score': 0, 'evidence': [], '_seen': set(), '_terms': defaultdict(set), '_relation_score': 0}
        return candidates[path]

    def add(path, kind, term, *, line=None, column=None, symbol_id=None):
        row = candidate(path); key = (kind, term)
        row['_terms'][kind].add(term)
        if key in row['_seen']: return
        row['_seen'].add(key)
        row['evidence'].append({'kind': kind, 'term': term, 'file': path, 'line': line, 'column': column,
            'symbol_id': symbol_id, 'source_sha256': row['sha256'], 'source_bytes': row['bytes'],
            'position_basis': 'path' if kind == 'path' else 'source'})
        if symbol_id: seed_ids.add(symbol_id)

    basenames = defaultdict(list)
    for path in sorted(contents): basenames[path.rsplit('/', 1)[-1]].append(path)
    ambiguous_mentions = set()
    for match, normalized in _path_mentions(issue):
        if '/' not in normalized:
            matches = basenames[normalized]; resolution = 'unique_basename'
        elif normalized in contents:
            matches = [normalized]; resolution = 'repository_relative'
        elif '\\' in match.group():
            matches = sorted(path for path in contents if '/' in path and normalized.endswith('/' + path))
            resolution = 'windows_suffix'
        else: continue
        if not matches: continue
        prefix_lines = re.split(r'\r\n|\r|\n', issue[:match.start()])
        position = {'term': match.group(), 'line': len(prefix_lines),
                    'column': len(prefix_lines[-1]) + 1,
                    'issue_span': {'start': match.start(), 'end': match.end()},
                    'issue_sha256': issue_sha256, 'position_basis': 'issue'}
        if len(matches) > 1:
            if match.group() not in ambiguous_mentions:
                gaps.append({'code': 'ambiguous_path_basename' if resolution == 'unique_basename' else 'ambiguous_path_suffix',
                             **position, 'paths': matches[:max_candidates], 'count': len(matches)})
                ambiguous_mentions.add(match.group())
            continue
        row = candidate(matches[0])
        if row['_terms']['explicit_path']: continue
        row['_terms']['explicit_path'].add(matches[0])
        row['evidence'].append({'kind': 'explicit_path', **position, 'file': matches[0],
            'source_sha256': row['sha256'], 'source_bytes': row['bytes'], 'resolution': resolution})

    for path, body in sorted(contents.items()):
        file_symbols = sorted(by_file[path], key=lambda symbol: (symbol.end-symbol.start, symbol.kind == 'file',
                                                                -symbol.start, symbol.symbol_id))
        module = next((symbol for symbol in file_symbols if symbol.kind == 'file'), None)
        path_offsets={}
        for word,offset in _word_spans(path): path_offsets.setdefault(word,offset)
        for term in terms:
            if term in path_offsets: add(path, 'path', term, column=path_offsets[term]+1,
                                          symbol_id=module.symbol_id if module else None)
        try:
            tokens, complete = _tokens(body.decode('utf-8'))
        except UnicodeError:
            tokens, complete = [], False
        if not complete: coverage['lexical_scan_errors'].append(path)
        for symbol in file_symbols:
            if symbol.kind not in ('function', 'async_function', 'class'): continue
            header = _header(tokens, symbol)
            names = [token.string for token in header if token.type == tokenize.NAME]
            _require(symbol.qualified_name.split('.')[-1] in names, 'index_symbol_source_binding')
            header_terms = set(_words(' '.join(names)))
            for term in terms:
                if term in _words(symbol.qualified_name.split('.')[-1]) and term in header_terms:
                    add(path, 'symbol', term, line=symbol.start, symbol_id=symbol.symbol_id)
                if term in _words(symbol.signature) and term in header_terms:
                    token,offset=next((token,offset) for token in header if token.type==tokenize.NAME
                                      for word,offset in _word_spans(token.string) if word==term)
                    line,column=_position(token,offset)
                    add(path, 'signature', term, line=line,column=column,symbol_id=symbol.symbol_id)
        for token in tokens:
            if token.type not in (tokenize.NAME, tokenize.STRING): continue
            owner = next((symbol for symbol in file_symbols if symbol.start <= token.start[0] <= symbol.end), module)
            symbol_id = owner.symbol_id if owner else None
            token_offsets={}
            for word,offset in _word_spans(token.string): token_offsets.setdefault(word,offset)
            for term in (term for term in terms if term in token_offsets):
                line,column=_position(token,token_offsets[term])
                add(path, 'body', term, line=line,column=column,symbol_id=symbol_id)
            if token.type == tokenize.STRING:
                for phrase in phrases:
                    offset = token.string.find(phrase)
                    if offset < 0: continue
                    prefix = token.string[:offset]
                    line = token.start[0] + prefix.count('\n')
                    column = len(prefix.rsplit('\n', 1)[-1])+1 if '\n' in prefix else token.start[1]+offset+1
                    add(path, 'exact_phrase', phrase, line=line, column=column, symbol_id=symbol_id)
    if coverage['lexical_scan_errors']:
        gaps.append({'code': 'lexical_scan_errors', 'files': coverage['lexical_scan_errors']})
    for row in candidates.values():
        groups = row['_terms']; distinct = set().union(*(values for key, values in groups.items()
                                                                       if key not in ('exact_phrase', 'explicit_path')))
        row['score'] = (bool(groups['explicit_path'])*PATH_ANCHOR_SCORE
                        + len(groups['exact_phrase'])*1_000_000 + len(distinct)*10_000
                        + len(groups['symbol'])*100 + len(groups['signature'])*10 + len(groups['path']))
    lexical_scores = {path: row['score'] for path, row in candidates.items()}
    edges = index.query(bundle.ref, IndexQuery(kind='relations', limit=max(1, len(index.relations))))
    _require(edges.next_cursor is None, 'index_relation_page')
    unresolved = 0
    for edge in edges.relations:
        if edge.relation_type not in ('calls', 'tests') or not ({edge.source_id, edge.target_ref} & seed_ids): continue
        if edge.resolution not in ('resolved_local', 'resolved_import'):
            if edge.resolution == 'unresolved': unresolved += 1
            continue
        _require(edge.source_id in symbols and edge.target_ref in symbols, 'index_relation_binding')
        for anchor, neighbor in ((edge.source_id, edge.target_ref), (edge.target_ref, edge.source_id)):
            if anchor not in seed_ids: continue
            path = symbols[neighbor].file; row = candidate(path)
            row['_relation_score'] = max(row['_relation_score'], lexical_scores[symbols[anchor].file]//2)
            proof = {'kind': 'static_call', 'relation_type': edge.relation_type, 'resolution': edge.resolution,
                'source_id': edge.source_id, 'target_ref': edge.target_ref, 'via_symbol_id': anchor,
                'file': edge.location.file, 'line': edge.location.start, 'end_line': edge.location.end,
                'source_sha256': sha256(contents[edge.location.file]).hexdigest(),
                'source_bytes': len(contents[edge.location.file]), 'basis': 'syntactic_not_runtime', 'hops': 1}
            if proof not in row['evidence']: row['evidence'].append(proof)
    if unresolved: gaps.append({'code': 'unresolved_calls_not_expanded', 'count': unresolved})
    for row in candidates.values(): row['score'] = max(row['score'], row['_relation_score'])
    ranked = sorted(candidates.values(), key=lambda row: (-row['score'], row['path']))
    tied = defaultdict(list)
    for row in ranked: tied[row['score']].append(row['path'])
    for score, paths in tied.items():
        if len(paths) > 1: gaps.append({'code': 'ambiguous_scores', 'score': score, 'paths': paths[:max_candidates], 'count': len(paths)})
    selected = ranked[:max_candidates]
    cutoff_tie = len(ranked) > max_candidates and ranked[max_candidates-1]['score'] == ranked[max_candidates]['score']
    if len(ranked) > max_candidates: gaps.append({'code': 'candidate_limit', 'matched_files': len(ranked), 'retained': len(selected)})
    if cutoff_tie: gaps.append({'code': 'candidate_tie_at_limit'})
    source_files = None; status = 'inconclusive'
    if not selected: gaps.append({'code': 'no_lexical_evidence'})
    else:
        proposed = {row['path']: row['sha256'] for row in selected}; budget_error = None
        try:
            _commitments(proposed)
            validate_files({path: contents[path] for path in proposed})
        except ContractError as error:
            budget_error = {'code': error.code, 'field': error.field}
            gaps.append({'code': 'source_profile_rejected', 'error': budget_error})
        incomplete = bool(missing_index or coverage['unreadable'] or coverage['scope_errors'] or coverage['lexical_scan_errors'])
        if not incomplete and not cutoff_tie:
            status = 'source_budget_exceeded' if budget_error else 'candidates_proposed'
            if not budget_error: source_files = proposed
    for row in selected:
        for key in ('_seen', '_terms', '_relation_score'): row.pop(key)
    index.store.verify(bundle.ref)
    _require(index.content_id == bundle.ref.content_id, 'snapshot_index_binding')
    return {'schema': 'macr.repair.source-proposal.v1', 'status': status, 'candidates': selected,
        'candidate_algorithm': 'lexical-static-path-anchor-v2',
        'source_files': source_files, 'terms': terms, 'quoted_phrases': phrases, 'coverage': coverage, 'gaps': gaps,
        'snapshot_content_id': bundle.ref.content_id, 'issue_sha256': issue_sha256,
        'limits': {'max_candidates': max_candidates, 'max_terms': 24, 'max_phrases': 8, 'max_call_hops': 1},
        'evidence_meaning': 'Lexical matches and syntactic call neighbors are candidates, not root causes.',
        'dependency_completeness': 'not_verified', 'task_ready': False, 'model_calls': 0, 'target_code_executed': False}
