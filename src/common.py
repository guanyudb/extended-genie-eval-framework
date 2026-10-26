"""Shared configuration and helpers for the Genie evaluation reference implementation (synthetic data only).

Configure with environment variables before running any step (see README.md):
  DATABRICKS_CONFIG_PROFILE   Databricks CLI profile for your workspace (default: DEFAULT)
  GENIE_EVAL_SCHEMA           catalog.schema where every table, view, and the model service are created
  GENIE_EVAL_WAREHOUSE_ID     ID of a serverless or pro SQL warehouse
  GENIE_EVAL_MODEL            hosted model for ai_query() (default: system.ai.claude-sonnet-4-6)
"""
import hashlib
import json
import os
import pathlib
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
SCHEMA = os.environ.get("GENIE_EVAL_SCHEMA", "")
WAREHOUSE_ID = os.environ.get("GENIE_EVAL_WAREHOUSE_ID", "")
MODEL = os.environ.get("GENIE_EVAL_MODEL", "system.ai.claude-sonnet-4-6")
STATE_FILE = pathlib.Path(__file__).with_name("state.json")

if not SCHEMA or not WAREHOUSE_ID:
    raise SystemExit("Set GENIE_EVAL_SCHEMA (catalog.schema) and GENIE_EVAL_WAREHOUSE_ID first; see README.md.")

w = WorkspaceClient(profile=PROFILE)


def sql(statement: str) -> list[dict]:
    """Run a statement on the configured warehouse and return rows as dicts."""
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="50s",
        disposition=Disposition.INLINE, format=Format.JSON_ARRAY)
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{resp.status.state}: {resp.status.error and resp.status.error.message}")
    if not resp.manifest or not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in resp.result.data_array]


def sql_str(value) -> str:
    """Render a Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def bench_id(test_id: str) -> str:
    """Deterministic 32-hex benchmark question ID derived from test_id."""
    return hashlib.md5(f"bench:{test_id}".encode()).hexdigest()


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(**kwargs) -> dict:
    state = load_state() | kwargs
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return state
