"""Small bounded repair I/O primitives, shared with the existing snapshot layer.

No target imports/execution, environment discovery or execution authority lives
here. The helpers are the narrow, dependency-free subset of the reviewed repair
preparation path needed by the normal CLI integration.
"""
from __future__ import annotations

import ast
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat

from macr.memory.atomic_file import atomic_write
from .scope import open_directory, safe_parent, validate_workspace_path
from .validation import ContractError


def _require(ok, field):
    if not ok: raise ContractError('repair_io_invalid', field)


def utf8_size(text):
    try: return len(text.encode('utf-8'))
    except UnicodeError: raise ContractError(field='utf8_text') from None


def encode_bounded(value, limit):
    _require(type(limit) is int and limit > 0, 'record_limit')
    data = bytearray()
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False,sort_keys=True,
                separators=(',', ':'),allow_nan=False).iterencode(value):
            raw = chunk.encode('utf-8')
            _require(len(data)+len(raw) <= limit, 'json_bytes')
            data.extend(raw)
        return bytes(data)
    except (TypeError,ValueError,UnicodeError,RecursionError) as error:
        if isinstance(error,ContractError): raise
        raise ContractError(field='json_serialization') from None


def bounded_json(value, *, max_bytes=128*1024, max_depth=32):
    _require(type(max_bytes) is int and max_bytes > 0
             and type(max_depth) is int and max_depth > 0, 'json_limit')
    def check(item, depth=0):
        _require(depth <= max_depth, 'json_depth')
        if item is None or type(item) in (str,bool,int): return
        if type(item) is float and math.isfinite(item): return
        if type(item) is list:
            for child in item: check(child,depth+1)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values(): check(child,depth+1)
            return
        raise ContractError(field='json_type')
    check(value)
    # Preserve the reviewed JSON contract's ASCII/default-separator size rule;
    # canonical storage separately uses compact UTF-8 in encode_bounded.
    try: size=len(json.dumps(value,ensure_ascii=True,allow_nan=False).encode('utf-8'))
    except (TypeError,ValueError,UnicodeError,OverflowError,RecursionError):
        raise ContractError(field='json_serialization') from None
    _require(size <= max_bytes,'json_bytes')
    return value


def load_json(text, *, max_bytes=128*1024, max_depth=32):
    _require(type(text) is str and utf8_size(text) <= max_bytes, 'json_bytes')
    def pairs(items):
        result = {}
        for k,v in items:
            _require(k not in result, 'duplicate_json_key')
            result[k] = v
        return result
    try:
        value=json.loads(text,object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ContractError(field='nonfinite')))
        return bounded_json(value,max_bytes=max_bytes,max_depth=max_depth)
    except ContractError: raise
    except (ValueError,UnicodeError,RecursionError): raise ContractError(field='json') from None


def safe_write(root, relative, data, *, limit):
    """Write only a regular leaf below an anchored parent; retain failed temps."""
    _require(type(data) is bytes and type(limit) is int and limit > 0
             and len(data) <= limit, 'storage_payload')
    with safe_parent(root,relative,create=True) as (parent,leaf):
        try:
            previous=os.stat(leaf,dir_fd=parent,follow_symlinks=False)
            _require(stat.S_ISREG(previous.st_mode), 'storage_leaf')
        except FileNotFoundError: pass
        atomic_write(Path(leaf),data,dir_fd=parent)


def _root(path):
    value=Path(path).absolute()
    validate_workspace_path(value,output=True)
    _require(value == value.resolve(), 'path_identity')
    with open_directory(value): pass
    return value


def _syntax(body,name):
    _require(type(body) is bytes and len(body) <= 16384, 'source_size')
    try:
        text=body.decode('utf-8');_require('\x00' not in text,'source_nul')
        tree=ast.parse(text,filename=name);pending=[(tree,0)];count=0
        while pending:
            node,depth=pending.pop();count+=1
            _require(count <= 2048 and depth <= 32,'source_ast_budget')
            pending.extend((child,depth+1) for child in ast.iter_child_nodes(node))
        compile(tree,name,'exec',dont_inherit=True,optimize=0)  # Discarded; never execute.
        return text,tree
    except (SyntaxError,ValueError,UnicodeError,RecursionError,MemoryError,OverflowError) as error:
        if isinstance(error,ContractError): raise
        raise ContractError('repair_io_invalid','source_compile') from None


def _hashes(files):
    return {name:sha256(body).hexdigest() for name,body in sorted(files.items())}


def _test_names(body):
    _,tree=_syntax(body,'checks.py');names=[]
    for node in tree.body:
        if isinstance(node,ast.ClassDef) and any(isinstance(base,ast.Attribute)
                and isinstance(base.value,ast.Name) and base.value.id=='unittest'
                and base.attr=='TestCase' for base in node.bases):
            names.extend(node.name+'.'+method.name for method in node.body
                if isinstance(method,ast.FunctionDef) and method.name.startswith('test_'))
    _require(1 <= len(names) <= 32 and len(names)==len(set(names)), 'declared_tests_missing_or_ambiguous')
    return sorted(names)
