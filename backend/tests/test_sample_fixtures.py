from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Locked Compose/Settings default (MAX_UPLOAD_BYTES). Do not invent a new limit.
_MAX_UPLOAD_BYTES = 2_000_000


def test_sample_fixtures_exist_and_fit_upload_limit() -> None:
    for name in ("sample-zh-CN.txt", "sample-en-US.txt"):
        data = (FIXTURES / name).read_bytes()
        assert data.strip()
        assert len(data) < _MAX_UPLOAD_BYTES
        data.decode("utf-8")
