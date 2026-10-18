import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def config(tmp_path):
    return Settings(_env_file=None, gemini_api_key="", database_path=tmp_path / "test.sqlite3")


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        client.headers["Authorization"] = "Bearer " + config.dev_auth_token
        yield client


@pytest.fixture
def session_body():
    return {"member_id": "M1002", "consent": True, "mode": "demo", "retain_transcript": True}
