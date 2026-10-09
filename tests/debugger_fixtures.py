"""Self-authored fixtures. Never import or execute an analysed repository."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT / "artifacts" / "test-runs"
_ACTIVE_ROOT: ContextVar[Path | None] = ContextVar("fixture_root", default=None)


@contextmanager
def fixture_session(root: Path):
    """Bind fixtures to an existing directory owned by the offline test entry."""
    root = Path(root).absolute()
    if root != root.resolve() or not root.is_relative_to(RUNS_ROOT) or root == RUNS_ROOT or not root.is_dir():
        raise ValueError("fixture session requires a test-run directory without links")
    token = _ACTIVE_ROOT.set(root)
    try:
        yield
    finally:
        _ACTIVE_ROOT.reset(token)


def fixture_root() -> Path:
    active = _ACTIVE_ROOT.get()
    if active is None:
        raise RuntimeError("managed fixture root missing; use scripts/run_offline_tests.py")
    if active != active.resolve() or not active.is_dir():
        raise RuntimeError("active fixture directory changed")
    root = active / str(uuid4())
    root.mkdir()
    return root


def write_files(root: Path, files: dict[str, str]) -> Path:
    active = _ACTIVE_ROOT.get()
    if active is None or not root.resolve().is_relative_to(active):
        raise RuntimeError("fixture writes require the active test-run directory")
    for relative, content in files.items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("fixture path must stay relative")
        path = root / relative
        if not path.resolve().is_relative_to(active):
            raise ValueError("fixture path escapes active directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
