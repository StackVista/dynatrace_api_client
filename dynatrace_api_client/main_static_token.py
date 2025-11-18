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

V2_ENTITIES_PATH = "api/v2/entities"
V2_PROCESS_SELECTOR = 'type("PROCESS_GROUP_INSTANCE")'
V2_PROCESS_GROUP_SELECTOR = 'type("PROCESS_GROUP")'


@dataclass
class EnvironmentConfig:
    name: str
    base_url: str
    api_token: str

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"{key} must be configured in the environment")
    return value


def load_configuration() -> Dict[str, object]:
    load_dotenv()

    # Single TEST environment for static token version
    test_base_url = _require_env("TEST_BASE_URL")
    test_api_token = _require_env("TEST_API_TOKEN")

    envs = [
        EnvironmentConfig(
            name="TEST",
            base_url=test_base_url,
            api_token=test_api_token,
        )
    ]

    relative_time_v2 = os.getenv("RELATIVE_TIME", "now-1h")
    process_fields = os.getenv(
        "PROCESS_FIELDS",
        "+fromRelationships,+toRelationships,+tags,+managementZones,+properties",
    )
    process_group_fields = os.getenv(
        "PROCESS_GROUP_FIELDS",
        "+fromRelationships,+toRelationships,+tags,+managementZones,+properties",
    )
    page_size = int(os.getenv("PAGE_SIZE", "50"))

    config = {
        "relative_time_v2": relative_time_v2,
        "process_fields": process_fields,
        "process_group_fields": process_group_fields,
        "page_size": page_size,
        "envs": envs,
    }
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Dynatrace process-related entities (v1/v2) using static API tokens."
    )
    parser.add_argument(
        "--entity-types",
        nargs="+",
        choices=["process", "process-group"],
        default=["process", "process-group"],
        help="Limit v2 collection to specific entity types. Defaults to process and process-group.",
    )
    return parser.parse_args()


class StaticTokenAuthenticator:
    """Simple authenticator that uses a static API token."""

    def __init__(self, api_token: str):
        self.api_token = api_token

    def get_token(self) -> str:
        return self.api_token

    def invalidate(self) -> None:
        # No-op for static tokens
        pass


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
    authenticator: StaticTokenAuthenticator,
    params: Optional[Dict[str, str]] = None,
) -> Dict:
    token = authenticator.get_token()
    headers = {"Authorization": f"Api-Token {token}"}
    response = session.get(url, params=params, headers=headers, timeout=30)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        error_message = response.text.strip()
        raise RuntimeError(
            f"Request to {url} failed with {response.status_code}: {error_message or exc}"
        ) from exc
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"Failed to parse JSON response from {url}") from exc


def fetch_paginated_v1(
    session: requests.Session,
    url: str,
    initial_params: Dict[str, str],
    authenticator: StaticTokenAuthenticator,
    api_name: str,
) -> List:
    """Fetch paginated v1 API responses. V1 APIs return arrays directly and use Next-Page-Key header."""
    aggregated_items = []
    params = dict(initial_params)
    token = authenticator.get_token()
    headers = {"Authorization": f"Api-Token {token}"}
    page_num = 1

    while True:
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
        page_count = 0
        if isinstance(page_data, list):
            page_count = len(page_data)
            aggregated_items.extend(page_data)
        else:
            # Fallback: if it's not a list, wrap it
            page_count = 1
            aggregated_items.append(page_data)

        print(f"{api_name}: page {page_num} returned {page_count} records")
        page_num += 1

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
    authenticator: StaticTokenAuthenticator,
    api_name: str,
) -> Dict:
    url = f"{base_url}/{V2_ENTITIES_PATH}"
    aggregated_entities = []
    meta: Optional[Dict] = None
    params = dict(initial_params)
    page_num = 1

    while True:
        response = fetch_json(session, url, authenticator, params=params)
        if meta is None:
            meta = {k: v for k, v in response.items() if k not in ("entities", "nextPageKey")}
        page_entities = response.get("entities", [])
        page_count = len(page_entities)
        aggregated_entities.extend(page_entities)
        print(f"{api_name}: page {page_num} returned {page_count} records")
        page_num += 1

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
    authenticator: StaticTokenAuthenticator,
    page_size: int,
) -> None:
    base = env.normalized_base_url
    params = {"relativeTime": "hour", "pageSize": str(page_size)}

    process_url = f"{base}/{V1_PROCESS_ENDPOINT}"
    process_group_url = f"{base}/{V1_PROCESS_GROUP_ENDPOINT}"

    process_content = fetch_paginated_v1(session, process_url, params, authenticator, "process_v1")
    print(f"process_v1: total {len(process_content)} records")
    dump_response(process_content, build_filename(env.name, "process_v1"))

    process_group_content = fetch_paginated_v1(
        session, process_group_url, params, authenticator, "process-group_v1"
    )
    print(f"process-group_v1: total {len(process_group_content)} records")
    dump_response(process_group_content, build_filename(env.name, "process-group_v1"))


def run_v2_calls(
    session: requests.Session,
    env: EnvironmentConfig,
    relative_time: str,
    process_fields: str,
    process_group_fields: str,
    include_process_groups: bool,
    include_processes: bool,
    authenticator: StaticTokenAuthenticator,
) -> None:
    base = env.normalized_base_url

    if include_processes:
        process_params = {
            "entitySelector": V2_PROCESS_SELECTOR,
            "from": relative_time,
            "fields": process_fields,
        }
        process_content = fetch_paginated_entities(
            session, base, process_params, authenticator, "process_v2"
        )
        process_count = len(process_content.get("entities", []))
        print(f"process_v2: total {process_count} records")
        dump_response(process_content, build_filename(env.name, "process_v2"))

    if include_process_groups:
        process_group_params = {
            "entitySelector": V2_PROCESS_GROUP_SELECTOR,
            "from": relative_time,
            "fields": process_group_fields,
        }
        process_group_content = fetch_paginated_entities(
            session, base, process_group_params, authenticator, "process-group_v2"
        )
        process_group_count = len(process_group_content.get("entities", []))
        print(f"process-group_v2: total {process_group_count} records")
        dump_response(process_group_content, build_filename(env.name, "process-group_v2"))


def main() -> None:
    args = parse_args()
    config = load_configuration()

    include_processes = "process" in args.entity_types
    include_process_groups = "process-group" in args.entity_types

    for env in config["envs"]:  # type: ignore[arg-type]
        session = create_session()
        authenticator = StaticTokenAuthenticator(env.api_token)

        run_v1_calls(session, env, authenticator, config["page_size"])  # type: ignore[index]
        run_v2_calls(
            session,
            env,
            relative_time=config["relative_time_v2"],  # type: ignore[index]
            process_fields=config["process_fields"],  # type: ignore[index]
            process_group_fields=config["process_group_fields"],  # type: ignore[index]
            include_process_groups=include_process_groups,
            include_processes=include_processes,
            authenticator=authenticator,
        )


if __name__ == "__main__":
    main()

