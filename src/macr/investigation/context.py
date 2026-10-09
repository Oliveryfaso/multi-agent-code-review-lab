"""Faithful bounded excerpts; no imports or execution of repository source."""
import io
import tokenize
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .records import ContextPack, ContextRequest, EvidenceRecord, IndexQuery, Location
from .validation import ContractError, decode_record


class ContextReader:
    def __init__(self, store, index=None, token_counter=None):
        self.store, self.index, self.token_counter = store, index, token_counter

    def read(self, ref, request: ContextRequest) -> ContextPack:
        request = decode_record(ContextRequest, asdict(request))
        pack = ContextPack(ref)
        pack.usage.tool_calls = 1
        remaining_bytes, remaining_tokens = request.max_bytes, request.max_tokens
        for location in request.locations:
            data = self.store.read_verified(ref, location.file)
            pack.bytes_read += self.store.last_usage.bytes_read
            try:
                encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0] if Path(location.file).suffix == ".py" else "utf-8"
                lines = data.decode(encoding).splitlines(keepends=True)
            except (UnicodeError, SyntaxError):
                raise ContractError("citation_invalid", "encoding") from None
            if location.end > len(lines):
                raise ContractError("citation_invalid", "line_range")
            if location.symbol_id:
                if self.index is None:
                    raise ContractError("citation_invalid", "symbol_id")
                symbols = self.index.query(ref, IndexQuery(symbol_ids=[location.symbol_id], limit=1)).symbols
                pack.bytes_read += self.index.last_usage.bytes_read
                pack.usage.tool_calls += self.index.last_usage.tool_calls
                if not symbols or symbols[0].file != location.file or location.start < symbols[0].start or location.end > symbols[0].end:
                    raise ContractError("citation_invalid", "symbol_binding")
            original = "".join(lines[location.start-1:location.end])
            # No tokenizer in A: byte cap is conservative excerpt sizing, not measured model usage.
            limit = min(remaining_bytes, remaining_tokens) if self.token_counter is None else remaining_bytes
            text = original.encode("utf-8")[:max(0, limit)].decode("utf-8", errors="ignore")
            if self.token_counter:
                while text and self._tokens(text) > remaining_tokens:
                    text = text[:-1]
            tokens = self._tokens(text) if self.token_counter else len(text.encode("utf-8"))
            remaining_bytes -= len(text.encode("utf-8"))
            remaining_tokens -= tokens
            if text != original:
                pack.truncations.append(f"{location.file}:excerpt_limit")
            if not text:
                continue
            end = min(location.end, location.start + len(text.splitlines()) - 1)
            actual = Location(location.file, location.start, end, location.symbol_id)
            pack.snippets.append({"location": asdict(actual), "requested_location": asdict(location), "text": text,
                                  "token_count_kind": "measured" if self.token_counter else "byte_estimate_excerpt_only"})
            pack.evidence.append(EvidenceRecord(str(uuid4()), ref.snapshot_id, "Read captured source" + (" (truncated)" if text != original else ""),
                                                actual, text, "source_excerpt", str(uuid4())))
        pack.usage.bytes_read = pack.bytes_read
        return pack

    def _tokens(self, text):
        count = self.token_counter(text)
        if type(count) is not int or count < 0:
            raise ContractError('token_counter_invalid')
        return count
