"""Enforce the package's import boundaries."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "airgap"


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return {name for name in imports if name.startswith("airgap.")}


def test_only_supervisor_imports_transport() -> None:
    for path in SRC.glob("*.py"):
        imports = _internal_imports(path)
        if path.name == "supervisor.py":
            continue
        assert "airgap.transport" not in imports, path


def test_pure_core_imports_only_vocab() -> None:
    for name in ("protocol.py", "policy.py"):
        assert _internal_imports(SRC / name) <= {"airgap.vocab"}


def test_vocab_has_no_internal_imports() -> None:
    assert not _internal_imports(SRC / "vocab.py")
