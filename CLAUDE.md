# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ibmcloud-local` is a local development emulator for IBM Cloud services (similar to LocalStack), allowing developers to test IBM Cloud applications locally. Python 3.12+, FastAPI-based. The primary implemented service is VPC; stubs exist for COS, PowerVS, IKS, and Code Engine.

## Commands

All tasks are managed via [mise](https://mise.jdx.dev/):

```bash
mise run install   # Install dependencies (editable + dev extras)
mise run dev       # Start with hot-reload (uvicorn --reload on port 4515)
mise run start     # Start via CLI wrapper
mise run test      # pytest tests/ -v
mise run lint      # ruff check src/ cli/ tests/
```

Direct equivalents if mise is unavailable:
```bash
uvicorn src.server:app --host 0.0.0.0 --port 4515 --reload
pytest tests/ -v
ruff check src/ cli/ tests/
```

Run a single test: `pytest tests/path/to/test_file.py::test_name -v`

Ruff line length is 100 characters (configured in `pyproject.toml`).

## Architecture

### Provider Pattern
Each IBM Cloud service is a `BaseProvider` subclass (`src/providers/base.py`). A provider:
- Owns a FastAPI `APIRouter` with all its routes
- Defines `service_name`, `api_version`, and `description` metadata
- Interacts only with its own namespace in `StateStore`

Add new services by subclassing `BaseProvider` and registering in `src/routing.py`. Stubs for future providers are referenced in `src/server.py`.

### State Management (`src/state/store.py`)
Singleton `StateStore` with namespaced, thread-safe key-value storage. Each resource gets an auto-generated ID, CRN, and href. Supports optional disk persistence (JSON snapshot). Configure via `IBMCLOUD_LOCAL_PERSISTENCE=memory|disk`.

### Route Registry (`src/routing.py`)
Traefik-inspired registry that tracks all providers and their routes. Powers the dashboard and the `ibmcloud-local routes` CLI command.

### Request Logging Middleware (`src/middleware/request_logger.py`)
Classifies every request by path prefix to its IBM Cloud service, stores up to 1000 entries, and adds `X-Emulator-Duration-Ms` / `X-Emulator-Service` response headers.

### Control Plane Endpoints
- `GET /api/dashboard` — overall status
- `GET /api/dashboard/routes` — routing table
- `GET /api/dashboard/requests` — live request feed
- `POST /_emulator/reset[/{namespace}]` — wipe state
- `GET /_emulator/state` — dump full state (debug)
- `GET /_emulator/health` — health check

### VPC Provider (`src/providers/vpc.py`)
The only fully implemented provider (~650 lines). Covers VPCs, Subnets, Instances (VSIs), Security Groups, and Floating IPs. Notable behaviors:
- Instance lifecycle uses async state machine: `pending → starting → running`
- CIDR overlap validation on subnet creation
- IDs use IBM Cloud regional prefix format (e.g., `r006-...`)
- Error responses match IBM Cloud API envelope: `{"errors": [{"code": ..., "message": ...}]}`

### Data Models (`src/models/vpc.py`)
Pydantic v2 models matching real IBM Cloud API shapes. Request models (e.g., `VpcCreate`) and response models (e.g., `Vpc`) are separate. Enums cover resource states.

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `IBMCLOUD_LOCAL_PORT` | `4515` | Server port |
| `IBMCLOUD_LOCAL_HOST` | `0.0.0.0` | Bind address |
| `IBMCLOUD_LOCAL_PERSISTENCE` | `memory` | `memory` or `disk` |
| `IBMCLOUD_LOCAL_DASHBOARD` | `true` | Enable dashboard endpoints |
| `IBMCLOUD_LOCAL_LOG_LEVEL` | `info` | Log verbosity |
