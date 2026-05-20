from __future__ import annotations

from time import perf_counter


class Timer:
    def __enter__(self):
        self.started = perf_counter()
        self.latency_ms = 0
        return self

    def __exit__(self, exc_type, exc, tb):
        self.latency_ms = self.elapsed_ms()

    def elapsed_ms(self) -> int:
        return int((perf_counter() - self.started) * 1000)
