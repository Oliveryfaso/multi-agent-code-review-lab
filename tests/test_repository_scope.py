import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from debugger_fixtures import fixture_root, write_files
from macr.investigation.records import ScopeProfile
from macr.investigation.validation import ContractError

try:
    from macr.investigation.scope import RepositoryScope, resolve_allowed
except ModuleNotFoundError:
    RepositoryScope = resolve_allowed = None


class RepositoryScopeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RepositoryScope, "unified repository scope is missing")
        self.root = write_files(fixture_root(), {"a.py": "x = 1\n", "pkg/b.py": "y = 2\n", ".env": "DO_NOT_READ", "._a.py": "APPLEDOUBLE", ".git/HEAD": "excluded", "artifacts/private.py": "excluded", "photo.bin": "unsupported"})

    def test_inventory_excludes_secret_without_open(self):
        original = Path.read_bytes
        def checked(path):
            self.assertNotIn(path.name, (".env", "._a.py", "HEAD", "private.py"))
            return original(path)
        with patch.object(Path, "read_bytes", checked):
            scope = RepositoryScope().inventory(self.root, ScopeProfile())
        self.assertEqual({f.path for f in scope.eligible}, {"a.py", "pkg/b.py"})
        self.assertTrue({".env", "._a.py", ".git", "artifacts"} <= {f.path for f in scope.excluded})
        self.assertEqual(scope.unsupported[0].path, "photo.bin")
        self.assertEqual(resolve_allowed(self.root, "a.py", scope), self.root / "a.py")
        for relative in ("../other.py", str(self.root / "a.py"), ".env", ".git/HEAD"):
            with self.assertRaises(ContractError):
                resolve_allowed(self.root, relative, scope)

    def test_limits_report_all_inventory_without_first_n_success(self):
        for profile in (replace(ScopeProfile(), max_python_files=1), replace(ScopeProfile(), max_python_lines=1), replace(ScopeProfile(), max_text_bytes=1), replace(ScopeProfile(), max_file_bytes=1)):
            scope = RepositoryScope().inventory(self.root, profile)
            self.assertIn("scope_limit", [e.code for e in scope.errors])
            self.assertTrue({"a.py", "pkg/b.py"} <= {f.path for f in scope.eligible + scope.unsupported})

    def test_unreadable_case_collision_and_links(self):
        from macr.investigation import scope as scope_module
        self.assertTrue(hasattr(scope_module, "read_safe"), "bounded no-follow reader is missing")
        original = scope_module.read_safe
        def unavailable(root, relative, limit):
            if relative == "a.py":
                raise PermissionError("fixture")
            return original(root, relative, limit)
        with patch("macr.investigation.scope.read_safe", unavailable):
            scope = RepositoryScope().inventory(self.root, ScopeProfile())
        self.assertEqual([f.path for f in scope.unreadable], ["a.py"])
        # exFAT cannot reliably create case variants or enforce chmod. Inject metadata only.
        with patch("macr.investigation.scope.walk_entries", return_value=[("a.py", "file", 1), ("A.py", "file", 1), ("link.py", "symlink", 1)]):
            scope = RepositoryScope().inventory(self.root, ScopeProfile())
        self.assertIn("case_collision", [e.code for e in scope.errors])
        self.assertNotIn("link.py", {f.path for f in scope.eligible})

    def test_reader_is_bounded_before_allocating_full_file(self):
        from macr.investigation import scope as scope_module
        self.assertTrue(hasattr(scope_module, "read_safe"), "bounded no-follow reader is missing")
        with self.assertRaises(ContractError):
            scope_module.read_safe(self.root, "a.py", 2)

    def test_scoped_search_literal_and_ast_cannot_escape(self):
        from macr.tools.search import TextSearchTool
        from macr.tools.ast_tool import PythonAstTool
        scope = RepositoryScope().inventory(self.root, ScopeProfile())
        result = TextSearchTool().run(self.root, "x = 1", scope=scope)
        self.assertEqual(result.data["matches"][0]["file"], "a.py")
        self.assertEqual(TextSearchTool().run(self.root, "[", scope=scope).data["matches"], [])
        self.assertFalse(PythonAstTool().run(self.root, "../outside.py", [], scope=scope).ok)

    def test_all_static_tools_share_allowed_files(self):
        import inspect
        from macr.tools.repo_map import RepoMapTool
        from macr.tools.symbol_graph import SymbolGraphTool
        from macr.tools.code_graph import CodeGraphTool
        write_files(self.root, {"artifacts/private.py": "def hidden(): pass\n"})
        scope = RepositoryScope().inventory(self.root, ScopeProfile())
        for tool in (RepoMapTool(), SymbolGraphTool(), CodeGraphTool(cache_dir=fixture_root())):
            with self.subTest(tool=tool.name):
                self.assertIn("scope", inspect.signature(tool.run).parameters, "static tool lacks unified scope")
                result = tool.run(self.root, [], scope=scope)
                self.assertTrue(result.ok)
                self.assertEqual(result.data["scope_file_count"], 2)
                if tool.name == "code_graph":
                    self.assertEqual({n["file"] for n in result.data["nodes"]}, {"a.py", "pkg/b.py"})
