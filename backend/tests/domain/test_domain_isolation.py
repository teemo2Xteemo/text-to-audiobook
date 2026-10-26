import ast
from pathlib import Path

from app.domain.errors import ErrorType

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "edge_tts",
        "fastapi",
        "ffmpeg",
        "httpx",
        "lingua",
        "ollama",
        "redis",
        "rq",
        "transformers",
        "torch",
        "langdetect",
        "uvicorn",
    }
)

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "app" / "domain"
APPLICATION_DIR = Path(__file__).resolve().parents[2] / "app" / "application"


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
        "WORKER_FAILED",
        "PROVIDER_CONFIGURATION_ERROR",
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


def test_application_modules_do_not_import_vendors() -> None:
    imported: set[str] = set()
    for path in APPLICATION_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(FORBIDDEN_IMPORT_ROOTS)


def test_job_service_does_not_import_infrastructure() -> None:
    path = APPLICATION_DIR / "jobs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert not any(m.startswith("app.infrastructure") for m in modules)


def test_application_modules_do_not_import_infrastructure() -> None:
    offenders: dict[str, set[str]] = {}
    for path in APPLICATION_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        infra = {m for m in modules if m.startswith("app.infrastructure")}
        if infra:
            offenders[path.relative_to(APPLICATION_DIR).as_posix()] = infra
    assert offenders == {}
