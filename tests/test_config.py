import base64
import json

import pytest

from app import config
from app.config import ConfigError, load_settings

SERVICE_ACCOUNT = {"type": "service_account", "project_id": "key-project", "private_key": "secret"}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "GOOGLE_CLOUD_PROJECT",
        "ANTHROPIC_API_KEY",
        "CLAUDE_EFFORT",
        "GOOGLE_CREDENTIALS_BASE64",
    ):
        monkeypatch.delenv(name, raising=False)
    # Ignore the developer's real .env: only the variables set by each test count.
    monkeypatch.setattr(config, "load_dotenv", lambda path: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


def _encoded(info) -> str:
    return base64.b64encode(json.dumps(info).encode()).decode()


def test_without_credentials_the_gcloud_login_is_used(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    settings = load_settings()
    assert (settings.gcp_project, settings.google_credentials) == ("my-project", None)


def test_service_account_key_supplies_credentials_and_its_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_BASE64", _encoded(SERVICE_ACCOUNT))
    settings = load_settings()
    assert settings.google_credentials == SERVICE_ACCOUNT
    assert settings.gcp_project == "key-project"


def test_explicit_project_wins_over_the_keys_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_BASE64", _encoded(SERVICE_ACCOUNT))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "other-project")
    assert load_settings().gcp_project == "other-project"


def test_credentials_never_appear_in_the_settings_repr(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_BASE64", _encoded(SERVICE_ACCOUNT))
    assert "secret" not in repr(load_settings())


@pytest.mark.parametrize(
    "value, message",
    [
        ("not base64!", "not a base64-encoded JSON file"),
        (base64.b64encode(b"not json").decode(), "not a base64-encoded JSON file"),
        (_encoded({"type": "external_account"}), "must contain a service-account key"),
        (_encoded(["a list"]), "must contain a service-account key"),
    ],
)
def test_invalid_credentials_are_rejected_without_echoing_them(monkeypatch, value, message):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_BASE64", value)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    with pytest.raises(ConfigError, match=message) as error:
        load_settings()
    assert value not in str(error.value) and error.value.__cause__ is None


def test_missing_project_and_key_is_reported():
    with pytest.raises(ConfigError, match="GOOGLE_CLOUD_PROJECT is not set"):
        load_settings()
