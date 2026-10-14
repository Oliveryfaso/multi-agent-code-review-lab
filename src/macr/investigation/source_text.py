"""Strict decoded source with preserved LF/CRLF/CR physical coordinates."""
from __future__ import annotations

import re
import codecs
import tokenize
from dataclasses import dataclass
from pathlib import PurePosixPath

from .validation import ContractError

PHYSICAL_LINE_VERSION = 'lf-crlf-cr-v1'
COORDINATE_ENCODING_VERSION = 'per-raw-line-codec-v1'


def physical_lines(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$', text))


def raw_physical_lines(data: bytes) -> tuple[bytes, ...]:
    return tuple(re.findall(rb'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$', data))


def raw_physical_line_count(data: bytes) -> int:
    return len(raw_physical_lines(data))


def _validate_coordinate_encoding(data: bytes, encoding: str, text: str):
    """A codec may not insert, remove or relocate a raw physical boundary."""
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
        parts = raw_physical_lines(data)
        decoded_parts = []
        for number, part in enumerate(parts):
            ending = '\r\n' if part.endswith(b'\r\n') else '\r' if part.endswith(b'\r') else '\n' if part.endswith(b'\n') else ''
            last = number == len(parts) - 1
            body_bytes = part[:-len(ending)] if ending else part
            body = decoder.decode(body_bytes, final=last and not ending)
            terminator = decoder.decode(ending.encode('ascii'), final=last) if ending else ''
            if terminator != ending or '\r' in body or '\n' in body:
                raise ContractError('unsupported_coordinate_encoding', 'source_encoding')
            decoded_parts.append(body + terminator)
        if ''.join(decoded_parts) != text:
            raise ContractError('unsupported_coordinate_encoding', 'source_encoding')
    except (UnicodeError, LookupError, TypeError):
        raise ContractError('unsupported_coordinate_encoding', 'source_encoding') from None


@dataclass(frozen=True)
class SourceExcerpt:
    text: str
    requested_start: int
    requested_end: int
    actual_start: int | None
    actual_end: int | None
    complete_range: bool
    truncation_reason: str = ''


@dataclass(frozen=True)
class SourceText:
    text: str
    encoding: str
    physical_lines: tuple[str, ...]

    @classmethod
    def from_bytes(cls, data: bytes, path: str):
        if type(data) is not bytes:
            raise ContractError(field='source_bytes')
        try:
            lines = iter(raw_physical_lines(data))
            # detect_encoding must see the second physical line even for CR-only input.
            encoding = tokenize.detect_encoding(lambda: next(lines, b'').replace(b'\r\n', b'\n').replace(b'\r', b'\n'))[0] if PurePosixPath(path).suffix == '.py' else 'utf-8'
            text = data.decode(encoding, errors='strict')
        except (UnicodeError, SyntaxError, LookupError):
            raise ContractError('encoding_error', 'source_encoding') from None
        _validate_coordinate_encoding(data, encoding, text)
        return cls(text, encoding, physical_lines(text))

    def excerpt(self, start: int, end: int, max_bytes: int | None = None) -> SourceExcerpt:
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(self.physical_lines):
            raise ContractError('citation_invalid', 'line_range')
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
            raise ContractError(field='max_bytes')
        original = ''.join(self.physical_lines[start - 1:end])
        text = original if max_bytes is None else original.encode('utf-8')[:max_bytes].decode('utf-8', errors='ignore')
        actual_end = start + len(physical_lines(text)) - 1 if text else None
        return SourceExcerpt(text, start, end, start if text else None, actual_end,
                             text == original, '' if text == original else 'excerpt_limit')

    def coordinates(self, line: int, utf8_column: int):
        if type(line) is not int or not 1 <= line <= len(self.physical_lines) or type(utf8_column) is not int or utf8_column < 0:
            raise ContractError('citation_invalid', 'utf8_column')
        raw = self.physical_lines[line - 1].rstrip('\r\n').encode('utf-8')
        if utf8_column > len(raw):
            raise ContractError('citation_invalid', 'utf8_column')
        try:
            raw[:utf8_column].decode('utf-8')
        except UnicodeError:
            raise ContractError('citation_invalid', 'utf8_column') from None
        return {'line': line, 'utf8_column': utf8_column}
