import ast
from pathlib import Path

from app.domain.errors import ErrorType

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "edge_tts",
        "fastapi",
        "ffmpeg",
        "httpx",
        "redis",
        "rq",
        "transformers",
        "uvicorn",
    }
)

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "app" / "domain"


def test_error_type_codes_match_backend_rule() -> None:
    assert {item.value for item in ErrorType} == {
        "INVALID_INPUT",
        "UNSUPPORTED_LANGUAGE",
        "TRANSLATION_FAILED",
        "TTS_FAILED",
        "AUDIO_PROCESSING_FAILED",
        "STORAGE_FAILED",
        "PROVIDER_RATE_LIMIT",
        "TIMEOUT",
    }


def test_domain_modules_do_not_import_vendors() -> None:
    imported: set[str] = set()
    for path in DOMAIN_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(FORBIDDEN_IMPORT_ROOTS)
