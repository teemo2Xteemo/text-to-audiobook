import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"
FORBIDDEN_ROOTS = frozenset({"edge_tts", "transformers", "torch"})
FORBIDDEN_NAMES = frozenset(
    {
        "Communicate",
        "AutoModelForSeq2SeqLM",
        "AutoTokenizer",
    }
)


def test_api_modules_do_not_import_vendor_sdks() -> None:
    """API routes must not import edge_tts, torch, or transformers."""
    paths = sorted(API_DIR.glob("*.py"))
    assert paths, f"no API modules under {API_DIR}"
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        imported_modules: set[str] = set()
        imported_names: set[str] = set()
        used_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
                imported_modules.add(node.module)
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                used_names.add(node.id)
        assert imported_roots.isdisjoint(FORBIDDEN_ROOTS), path
        assert all(
            not any(part in FORBIDDEN_ROOTS for part in module.split("."))
            for module in imported_modules
        ), path
        assert imported_names.isdisjoint(FORBIDDEN_NAMES), path
        assert used_names.isdisjoint(FORBIDDEN_NAMES), path
