# Dynatrace API Client

A Python client for fetching and processing topology data from Dynatrace APIs. This tool provides multiple workflows for accessing Dynatrace instances using different authentication methods and processing raw API responses into structured topology formats.

## Features

- **Multiple Authentication Methods**: OAuth/JWT (Microsoft) and static API token support
- **Dual API Support**: Works with both Dynatrace API v1 and v2
- **Pagination**: Automatic pagination handling for both API versions
- **Entity Types**: Supports process, process-group, and host entities
- **Topology Processing**: Converts raw Dynatrace JSON into structured topology format compatible with StackState
- **Configurable**: Environment-based configuration via `.env` file
- **Multi-Environment**: Support for multiple Dynatrace environments (PA, Prod, Test)

## Installation

### Prerequisites

- Python 3.8 or higher
- Access to Dynatrace tenant(s)
- OAuth credentials (for `main.py`) or API token (for `main_static_token.py`)

### Setup

1. Clone the repository:
```bash
git clone git@github.com:StackVista/dynatrace_api_client.git
cd dynatrace_api_client
```

2. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Copy the example environment file and configure it:
```bash
cp env.example .env
# Edit .env with your configuration
```

## Configuration

### OAuth/JWT Authentication (`main.py`)

For production environments using Microsoft OAuth/JWT authentication:

```env
# Dynatrace tenant base URLs
PA_BASE_URL=https://your-pa-tenant.live.dynatrace.com
PROD_BASE_URL=https://your-prod-tenant.live.dynatrace.com

# OAuth / JWT configuration for PA
PA_AUTH_URL=https://login.microsoftonline.com/your-pa-tenant/oauth2/v2.0/token
PA_AUTH_CLIENT_ID=your-client-id
PA_AUTH_CLIENT_SECRET=your-client-secret
PA_AUTH_SCOPE=api://your-app-id/.default  # Optional
PA_AUTH_RESOURCE=https://your-resource     # Optional
PA_AUTH_AUDIENCE=https://your-resource     # Optional

# OAuth / JWT configuration for Prod
PROD_AUTH_URL=https://login.microsoftonline.com/your-prod-tenant/oauth2/v2.0/token
PROD_AUTH_CLIENT_ID=your-client-id
PROD_AUTH_CLIENT_SECRET=your-client-secret
PROD_AUTH_SCOPE=api://your-app-id/.default  # Optional
PROD_AUTH_RESOURCE=https://your-resource     # Optional
PROD_AUTH_AUDIENCE=https://your-resource     # Optional
```

### Static API Token Authentication (`main_static_token.py`)

For test environments using static API tokens:

```env
# Required configuration for TEST environment
TEST_BASE_URL=https://abc12345.live.dynatrace.com
TEST_API_TOKEN=dt0c01.YOUR_API_TOKEN_HERE
```

### Optional Configuration

All workflows support these optional settings:

```env
# Relative time window for v2 API queries (default: now-1h)
RELATIVE_TIME=now-1h

# Page size for v1 API pagination (default: 50)
PAGE_SIZE=50

# Field lists for v2 API queries
PROCESS_FIELDS=+fromRelationships,+toRelationships,+tags,+managementZones,+properties
PROCESS_GROUP_FIELDS=+fromRelationships,+toRelationships,+tags,+managementZones,+properties
HOST_FIELDS=+fromRelationships,+toRelationships,+tags,+managementZones,+properties
```

## Usage

### Workflow 1: OAuth/JWT Authentication (`main.py`)

Fetches data from PA and Prod environments using OAuth/JWT authentication.

**Basic usage:**
```bash
python -m dynatrace_api_client.main
```

This will fetch process and process-group data from both PA and Prod environments using both v1 and v2 APIs.

**Specify entity types:**
```bash
# Fetch only hosts
python -m dynatrace_api_client.main --entity-types host

# Fetch multiple entity types
python -m dynatrace_api_client.main --entity-types process process-group host

# Fetch only processes
python -m dynatrace_api_client.main --entity-types process
```

**Output files:**
- `PA_process_v1_<timestamp>.json`
- `PA_process-group_v1_<timestamp>.json`
- `PA_host_v1_<timestamp>.json`
- `PA_process_v2_<timestamp>.json`
- `PA_process-group_v2_<timestamp>.json`
- `PA_host_v2_<timestamp>.json`
- `Prod_process_v1_<timestamp>.json`
- `Prod_process-group_v1_<timestamp>.json`
- `Prod_host_v1_<timestamp>.json`
- `Prod_process_v2_<timestamp>.json`
- `Prod_process-group_v2_<timestamp>.json`
- `Prod_host_v2_<timestamp>.json`

### Workflow 2: Static API Token (`main_static_token.py`)

Fetches data from a TEST environment using a static API token. Includes per-page record counts in the output.

**Basic usage:**
```bash
python -m dynatrace_api_client.main_static_token
```

**Specify entity types:**
```bash
# Fetch only hosts
python -m dynatrace_api_client.main_static_token --entity-types host

# Fetch multiple entity types
python -m dynatrace_api_client.main_static_token --entity-types process process-group host
```

**Output files:**
- `TEST_process_v1_<timestamp>.json`
- `TEST_process-group_v1_<timestamp>.json`
- `TEST_host_v1_<timestamp>.json`
- `TEST_process_v2_<timestamp>.json`
- `TEST_process-group_v2_<timestamp>.json`
- `TEST_host_v2_<timestamp>.json`

**Example output:**
```
process_v1: page 1 returned 50 records
process_v1: page 2 returned 30 records
process_v1: total records: 80
Wrote /path/to/TEST_process_v1_1234567890.json
```

### Workflow 3: Topology Processing (`main_process_topology.py`)

Processes raw Dynatrace JSON files into a structured topology format compatible with StackState integrations.

**Basic usage:**
```bash
python -m dynatrace_api_client.main_process_topology Prod_process_v2_1234567890.json
```

**Options:**
```bash
# Specify component type explicitly
python -m dynatrace_api_client.main_process_topology input.json --component-type process

# Custom output suffix
python -m dynatrace_api_client.main_process_topology input.json --output-suffix processed
```

**What it does:**
- Extracts entities from v1 (array) or v2 (dict with "entities" key) JSON formats
- Cleans metadata (converts float/bool/int to strings, handles nested properties)
- Creates URN-style identifiers (`urn:dynatrace:/{entityId}`)
- Extracts tags and labels (from tags, managementZones, softwareTechnologies, monitoringState)
- Normalizes v2 process-group payloads to v1-style shape
- Extracts and structures relationships (fromRelationships, toRelationships)

**Output format:**
```json
{
  "metadata": {
    "source_file": "input.json",
    "component_type": "process",
    "timestamp": 1234567890,
    "component_count": 100,
    "relationship_count": 250
  },
  "components": [
    {
      "entityId": "PROCESS_GROUP_INSTANCE-123",
      "displayName": "My Process",
      "identifiers": ["urn:dynatrace:/PROCESS_GROUP_INSTANCE-123"],
      "tags": ["tag1", "tag2", "managementZones:PROD"],
      ...
    }
  ],
  "relationships": [
    {
      "source": "PROCESS_GROUP_INSTANCE-123",
      "target": "HOST-456",
      "type": "runsOn"
    }
  ]
}
```

**Output files:**
- `{input_filename}_topology_{timestamp}.json`

## API Details

### Supported Entity Types

- **process**: Process group instances (`PROCESS_GROUP_INSTANCE`)
- **process-group**: Process groups (`PROCESS_GROUP`)
- **host**: Hosts (`HOST`)

### API Versions

#### API v1
- Endpoints:
  - `/api/v1/entity/infrastructure/processes`
  - `/api/v1/entity/infrastructure/process-groups`
  - `/api/v1/entity/infrastructure/hosts`
- Pagination: Uses `Next-Page-Key` header and `nextPageKey` query parameter
- Response format: Direct array of entities

#### API v2
- Endpoint: `/api/v2/entities`
- Entity selectors:
  - `type("PROCESS_GROUP_INSTANCE")`
  - `type("PROCESS_GROUP")`
  - `type("HOST")`
- Pagination: Uses `nextPageKey` in response body
- Response format: `{"entities": [...], "nextPageKey": "..."}`

### Pagination

Both API versions support automatic pagination:
- **v1**: Preserves `pageSize` parameter across pages, uses `Next-Page-Key` header
- **v2**: Uses `nextPageKey` from response body
- Configurable via `PAGE_SIZE` environment variable (default: 50)

## Project Structure

```
dynatrace_api_client/
├── dynatrace_api_client/
│   ├── __init__.py
│   ├── main.py                    # OAuth/JWT workflow
│   ├── main_static_token.py       # Static token workflow
│   └── main_process_topology.py   # Topology processing workflow
├── tests/
│   └── test_main.py
├── docker/
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── env.example
├── .gitignore
└── README.md
```

## Development

### Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

### Code Style

The project follows standard Python conventions. Consider using:
- `black` for code formatting
- `flake8` or `pylint` for linting
- `mypy` for type checking

### Docker

A Dockerfile is provided for containerized execution. Build and run:

```bash
docker build -t dynatrace-api-client .
docker run --env-file .env dynatrace-api-client
```

## Troubleshooting

### Authentication Errors

**OAuth/JWT (`main.py`):**
- Verify `AUTH_URL`, `CLIENT_ID`, and `CLIENT_SECRET` are correct
- Check that the OAuth application has the necessary permissions
- Ensure the token endpoint is accessible from your network

**Static Token (`main_static_token.py`):**
- Verify `TEST_API_TOKEN` is valid and has not expired
- Check that the token has the required scopes (e.g., `entities.read`)
- Ensure the token format is correct: `dt0c01.XXXXX...`

### API Errors

**400 Bad Request:**
- Verify entity types are valid (e.g., `PROCESS` is not a valid v2 entity type)
- Check that the entity selector syntax is correct
- Ensure required fields are included in the request

**401 Unauthorized:**
- Token may have expired (OAuth tokens auto-refresh)
- API token may be invalid or revoked
- Check token permissions and scopes

**429 Too Many Requests:**
- Implement rate limiting or retry logic
- Reduce `PAGE_SIZE` to make fewer requests

### Pagination Issues

- If pagination fails, check that `PAGE_SIZE` is within API limits (typically 1-400)
- Verify that the `Next-Page-Key` header (v1) or `nextPageKey` field (v2) is being handled correctly

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

See [LICENSE](LICENSE) file for details.

## Related Projects

This client is designed to work with:
- [StackState Dynatrace Integration](https://github.com/StackVista/stackstate-agent-integrations/tree/master/dynatrace_topology) - StackState AgentCheck for Dynatrace topology

## Support

For issues, questions, or contributions, please open an issue on GitHub.

