import itertools
from typing import Dict

from dynatrace_api_client.main import (
    AuthSettings,
    JwtAuthenticator,
    EnvironmentConfig,
    build_filename,
    fetch_json,
    fetch_paginated_entities,
    load_configuration,
    parse_args,
)


class DummyResponse:
    def __init__(self, payload: Dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class DummySession:
    def __init__(self, responses):
        self.responses = itertools.cycle(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return next(self.responses)


def test_load_configuration(monkeypatch):
    monkeypatch.setenv("PA_BASE_URL", "https://pa.example.com")
    monkeypatch.setenv("PROD_BASE_URL", "https://prod.example.com")
    monkeypatch.setenv("PA_AUTH_URL", "https://login.microsoftonline.com/pa/oauth2/v2.0/token")
    monkeypatch.setenv("PA_AUTH_CLIENT_ID", "pa-client")
    monkeypatch.setenv("PA_AUTH_CLIENT_SECRET", "pa-secret")
    monkeypatch.setenv("PROD_AUTH_URL", "https://login.microsoftonline.com/prod/oauth2/v2.0/token")
    monkeypatch.setenv("PROD_AUTH_CLIENT_ID", "prod-client")
    monkeypatch.setenv("PROD_AUTH_CLIENT_SECRET", "prod-secret")

    config = load_configuration()

    assert config["relative_time_v2"] == "now-1h"
    assert len(config["envs"]) == 2

    pa_env = config["envs"][0]
    assert isinstance(pa_env, EnvironmentConfig)
    assert pa_env.normalized_base_url == "https://pa.example.com"
    assert pa_env.auth.client_id == "pa-client"
    assert pa_env.auth.client_secret == "pa-secret"

    prod_env = config["envs"][1]
    assert prod_env.normalized_base_url == "https://prod.example.com"
    assert prod_env.auth.client_id == "prod-client"
    assert config["process_entity_fields"]


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog"])
    args = parse_args()
    assert set(args.entity_types) == {"process", "process-group", "process-entity"}


def test_parse_args_subset(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--entity-types", "process", "process-entity"])
    args = parse_args()
    assert set(args.entity_types) == {"process", "process-entity"}


def test_jwt_authenticator_refresh_and_reuse(monkeypatch):
    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append({"url": url, "data": data})
        token_value = f"token-{len(calls)}"
        payload = {"access_token": token_value, "expires_in": 60}
        return DummyResponse(payload)

    monkeypatch.setattr("requests.post", fake_post, raising=False)

    auth = JwtAuthenticator(
        AuthSettings(
            url="https://login.microsoftonline.com/test/oauth2/v2.0/token",
            client_id="client",
            client_secret="secret",
        )
    )

    first = auth.get_token()
    second = auth.get_token()
    auth.invalidate()
    third = auth.get_token()

    assert first == second == "token-1"
    assert third == "token-2"
    assert len(calls) == 2


def test_fetch_json_retries_on_401(monkeypatch):
    responses = [
        DummyResponse({"error": "Unauthorized"}, status_code=401),
        DummyResponse({"data": "ok"}, status_code=200),
    ]
    session = DummySession(responses)

    class StubAuthenticator:
        def __init__(self):
            self.invalidate_calls = 0
            self.tokens = iter(["token-1", "token-2"])

        def get_token(self):
            return next(self.tokens)

        def invalidate(self):
            self.invalidate_calls += 1

    authenticator = StubAuthenticator()

    result = fetch_json(session, "https://example.com/api", authenticator)

    assert result == {"data": "ok"}
    assert authenticator.invalidate_calls == 1
    assert len(session.calls) == 2
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token-1"
    assert session.calls[1]["headers"]["Authorization"] == "Bearer token-2"


def test_fetch_paginated_entities():
    responses = [
        DummyResponse(
            {
                "entities": [{"id": "E1"}],
                "nextPageKey": "abc",
                "totalCount": 2,
                "pageSize": 1,
            }
        ),
        DummyResponse(
            {
                "entities": [{"id": "E2"}],
                "totalCount": 2,
                "pageSize": 1,
            }
        ),
    ]
    session = DummySession(responses)

    class StubAuthenticator:
        def __init__(self):
            self.tokens = iter(["token-1", "token-2"])

        def get_token(self):
            try:
                return next(self.tokens)
            except StopIteration:
                return "token-final"

        def invalidate(self):
            raise AssertionError("invalidate should not be called for successful pagination")

    authenticator = StubAuthenticator()

    result = fetch_paginated_entities(
        session,
        "https://example.com",
        {
            "entitySelector": 'type("PROCESS_GROUP_INSTANCE")',
            "from": "now-1h",
            "fields": "+tags",
        },
        authenticator,
    )

    assert result["entities"] == [{"id": "E1"}, {"id": "E2"}]
    assert result["totalCount"] == 2
    assert "nextPageKey" not in result
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["entitySelector"] == 'type("PROCESS_GROUP_INSTANCE")'
    assert session.calls[1]["params"] == {"nextPageKey": "abc"}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token-1"
    assert session.calls[1]["headers"]["Authorization"] == "Bearer token-2"


def test_build_filename_has_timestamp():
    name = build_filename("PA", "process_v1")
    assert name.name.startswith("PA_process_v1_")
    assert name.suffix == ".json"

