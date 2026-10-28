from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(_env_file=None, storage_path=tmp_path)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
