import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

import requests
from dotenv import load_dotenv


V1_PROCESS_ENDPOINT = "api/v1/entity/infrastructure/processes"
V1_PROCESS_GROUP_ENDPOINT = "api/v1/entity/infrastructure/process-groups"
V1_HOST_ENDPOINT = "api/v1/entity/infrastructure/hosts"

V2_ENTITIES_PATH = "api/v2/entities"
V2_PROCESS_SELECTOR = 'type("PROCESS_GROUP_INSTANCE")'
V2_PROCESS_GROUP_SELECTOR = 'type("PROCESS_GROUP")'
V2_HOST_SELECTOR = 'type("HOST")'


@dataclass
class AuthSettings:
    url: str
    client_id: str
    client_secret: str
    scope: Optional[str] = None
    resource: Optional[str] = None
    audience: Optional[str] = None


@dataclass
class EnvironmentConfig:
    name: str
    base_url: str
    auth: AuthSettings

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"{key} must be configured in the environment")
    return value


def _build_auth_settings(prefix: str) -> AuthSettings:
    return AuthSettings(
        url=_require_env(f"{prefix}_AUTH_URL"),
        client_id=_require_env(f"{prefix}_AUTH_CLIENT_ID"),
        client_secret=_require_env(f"{prefix}_AUTH_CLIENT_SECRET"),
        scope=os.getenv(f"{prefix}_AUTH_SCOPE"),
        resource=os.getenv(f"{prefix}_AUTH_RESOURCE"),
        audience=os.getenv(f"{prefix}_AUTH_AUDIENCE"),
    )


def load_configuration() -> Dict[str, object]:
    load_dotenv()

    pa_base_url = _require_env("PA_BASE_URL")
    prod_base_url = _require_env("PROD_BASE_URL")

    relative_time_v2 = os.getenv("RELATIVE_TIME", "now-1h")
    process_fields = os.getenv(
        "PROCESS_FIELDS",
        "+fromRelationships,+toRelationships,+tags,+managementZones,+properties",
    )
    process_group_fields = os.getenv(
        "PROCESS_GROUP_FIELDS",
        "+fromRelationships,+toRelationships,+tags,+managementZones,+properties",
    )
    host_fields = os.getenv(
        "HOST_FIELDS",
        "+fromRelationships,+toRelationships,+tags,+managementZones,+properties",
    )
    page_size = int(os.getenv("PAGE_SIZE", "50"))

    config = {
        "relative_time_v2": relative_time_v2,
        "process_fields": process_fields,
        "process_group_fields": process_group_fields,
        "host_fields": host_fields,
        "page_size": page_size,
        "envs": [
            EnvironmentConfig(
                name="PA",
                base_url=pa_base_url,
                auth=_build_auth_settings("PA"),
            ),
            EnvironmentConfig(
                name="Prod",
                base_url=prod_base_url,
                auth=_build_auth_settings("PROD"),
            ),
        ],
    }
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Dynatrace process-related entities (v1/v2).")
    parser.add_argument(
        "--entity-types",
        nargs="+",
        choices=["process", "process-group", "host"],
        default=["process", "process-group"],
        help="Limit v2 collection to specific entity types. Defaults to process and process-group.",
    )
    return parser.parse_args()


class JwtAuthenticator:
    def __init__(self, auth_config: AuthSettings):
        self.auth_config = auth_config
        self._token: Optional[str] = None
        self._expiry_epoch: float = 0

    def get_token(self) -> str:
        now = time.time()
        if not self._token or now >= self._expiry_epoch:
            self._refresh_token()
        return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        self._token = None
        self._expiry_epoch = 0

    def _refresh_token(self) -> None:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.auth_config.client_id,
            "client_secret": self.auth_config.client_secret,
        }
        if self.auth_config.scope:
            data["scope"] = self.auth_config.scope
        if self.auth_config.resource:
            data["resource"] = self.auth_config.resource
        if self.auth_config.audience:
            data["audience"] = self.auth_config.audience

        response = requests.post(self.auth_config.url, data=data, timeout=30)
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("JWT auth response did not contain 'access_token'")

        expires_in = int(payload.get("expires_in", 300))
        self._expiry_epoch = time.time() + max(expires_in - 30, 30)
        self._token = token


def build_filename(system: str, data_type: str) -> Path:
    timestamp = int(time.time())
    filename = f"{system}_{data_type}_{timestamp}.json"
    return Path.cwd() / filename


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
        }
    )
    return session


def fetch_json(
    session: requests.Session,
    url: str,
    authenticator: JwtAuthenticator,
    params: Optional[Dict[str, str]] = None,
) -> Dict:
    def do_request() -> requests.Response:
        token = authenticator.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = session.get(url, params=params, headers=headers, timeout=30)
        return response

    response = do_request()
    if response.status_code == 401:
        authenticator.invalidate()
        response = do_request()

    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Failed to parse JSON response from {url}") from exc


def fetch_paginated_v1(
    session: requests.Session,
    url: str,
    initial_params: Dict[str, str],
    authenticator: JwtAuthenticator,
) -> List:
    """Fetch paginated v1 API responses. V1 APIs return arrays directly and use Next-Page-Key header."""
    aggregated_items = []
    params = dict(initial_params)
    token = authenticator.get_token()
    headers = {"Authorization": f"Bearer {token}"}

    while True:
        response = session.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 401:
            authenticator.invalidate()
            token = authenticator.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            response = session.get(url, params=params, headers=headers, timeout=30)

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            error_message = response.text.strip()
            raise RuntimeError(
                f"Request to {url} failed with {response.status_code}: {error_message or exc}"
            ) from exc

        try:
            page_data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Failed to parse JSON response from {url}") from exc

        # V1 APIs return arrays directly
        if isinstance(page_data, list):
            aggregated_items.extend(page_data)
        else:
            # Fallback: if it's not a list, wrap it
            aggregated_items.append(page_data)

        # Check for next page key in response header
        next_page_key = response.headers.get("Next-Page-Key")
        if not next_page_key:
            break
        # Preserve original params (like pageSize) when using nextPageKey
        params = dict(initial_params)
        params["nextPageKey"] = next_page_key

    return aggregated_items


def fetch_paginated_entities(
    session: requests.Session,
    base_url: str,
    initial_params: Dict[str, str],
    authenticator: JwtAuthenticator,
) -> Dict:
    url = f"{base_url}/{V2_ENTITIES_PATH}"
    aggregated_entities = []
    meta: Optional[Dict] = None
    params = dict(initial_params)

    while True:
        response = fetch_json(session, url, authenticator, params=params)
        if meta is None:
            meta = {k: v for k, v in response.items() if k not in ("entities", "nextPageKey")}
        aggregated_entities.extend(response.get("entities", []))
        next_key = response.get("nextPageKey")
        if not next_key:
            break
        params = {"nextPageKey": next_key}

    if meta is None:
        meta = {}
    result = dict(meta)
    result["entities"] = aggregated_entities
    return result


def dump_response(content: Union[Dict, List], filepath: Path) -> None:
    filepath.write_text(json.dumps(content, indent=2))
    print(f"Wrote {filepath}")


def run_v1_calls(
    session: requests.Session,
    env: EnvironmentConfig,
    authenticator: JwtAuthenticator,
    page_size: int,
    include_processes: bool,
    include_process_groups: bool,
    include_hosts: bool,
) -> None:
    base = env.normalized_base_url
    params = {"relativeTime": "hour", "pageSize": str(page_size)}

    if include_processes:
        process_url = f"{base}/{V1_PROCESS_ENDPOINT}"
        process_content = fetch_paginated_v1(session, process_url, params, authenticator)
        dump_response(process_content, build_filename(env.name, "process_v1"))

    if include_process_groups:
        process_group_url = f"{base}/{V1_PROCESS_GROUP_ENDPOINT}"
        process_group_content = fetch_paginated_v1(session, process_group_url, params, authenticator)
        dump_response(process_group_content, build_filename(env.name, "process-group_v1"))

    if include_hosts:
        host_url = f"{base}/{V1_HOST_ENDPOINT}"
        host_content = fetch_paginated_v1(session, host_url, params, authenticator)
        dump_response(host_content, build_filename(env.name, "host_v1"))


def run_v2_calls(
    session: requests.Session,
    env: EnvironmentConfig,
    relative_time: str,
    process_fields: str,
    process_group_fields: str,
    host_fields: str,
    include_process_groups: bool,
    include_processes: bool,
    include_hosts: bool,
    authenticator: JwtAuthenticator,
) -> None:
    base = env.normalized_base_url

    if include_processes:
        process_params = {
            "entitySelector": V2_PROCESS_SELECTOR,
            "from": relative_time,
            "fields": process_fields,
        }
        process_content = fetch_paginated_entities(session, base, process_params, authenticator)
        dump_response(process_content, build_filename(env.name, "process_v2"))

    if include_process_groups:
        process_group_params = {
            "entitySelector": V2_PROCESS_GROUP_SELECTOR,
            "from": relative_time,
            "fields": process_group_fields,
        }
        process_group_content = fetch_paginated_entities(session, base, process_group_params, authenticator)
        dump_response(process_group_content, build_filename(env.name, "process-group_v2"))

    if include_hosts:
        host_params = {
            "entitySelector": V2_HOST_SELECTOR,
            "from": relative_time,
            "fields": host_fields,
        }
        host_content = fetch_paginated_entities(session, base, host_params, authenticator)
        dump_response(host_content, build_filename(env.name, "host_v2"))


def main() -> None:
    args = parse_args()
    config = load_configuration()

    include_processes = "process" in args.entity_types
    include_process_groups = "process-group" in args.entity_types
    include_hosts = "host" in args.entity_types

    for env in config["envs"]:  # type: ignore[arg-type]
        session = create_session()
        authenticator = JwtAuthenticator(env.auth)

        run_v1_calls(
            session,
            env,
            authenticator,
            config["page_size"],  # type: ignore[index]
            include_processes=include_processes,
            include_process_groups=include_process_groups,
            include_hosts=include_hosts,
        )
        run_v2_calls(
            session,
            env,
            relative_time=config["relative_time_v2"],  # type: ignore[index]
            process_fields=config["process_fields"],  # type: ignore[index]
            process_group_fields=config["process_group_fields"],  # type: ignore[index]
            host_fields=config["host_fields"],  # type: ignore[index]
            include_process_groups=include_process_groups,
            include_processes=include_processes,
            include_hosts=include_hosts,
            authenticator=authenticator,
        )



