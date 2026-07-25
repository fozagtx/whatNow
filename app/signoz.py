"""Block 1: query layer over the SigNoz v5 query_range API.

Endpoint: POST {SIGNOZ_URL}/api/v5/query_range
Auth:     SIGNOZ-API-KEY header (Settings -> API Keys, admin role)
Docs:     https://signoz.io/docs/traces-management/trace-api/payload-model/
"""

import os
import time

import httpx

QUERY_RANGE_PATH = "/api/v5/query_range"

SPAN_FIELDS = [
    {"name": "timestamp", "fieldContext": "span"},
    {"name": "service.name", "fieldContext": "resource", "fieldDataType": "string"},
    {"name": "name", "fieldContext": "span", "fieldDataType": "string"},
    {"name": "duration_nano", "fieldContext": "span", "fieldDataType": "int64"},
    {"name": "has_error", "fieldContext": "span", "fieldDataType": "bool"},
    {"name": "status_message", "fieldContext": "span", "fieldDataType": "string"},
    {"name": "trace_id", "fieldContext": "span", "fieldDataType": "string"},
    {"name": "span_id", "fieldContext": "span", "fieldDataType": "string"},
]


def base_url() -> str:
    return os.getenv("SIGNOZ_URL", "http://localhost:8080").rstrip("/")


def _headers() -> dict:
    return {
        "SIGNOZ-API-KEY": os.getenv("SIGNOZ_API_KEY", ""),
        "Content-Type": "application/json",
    }


def query_range(request_type: str, queries: list[dict], minutes: int,
                end_offset_minutes: int = 0) -> dict:
    end = int(time.time() * 1000) - end_offset_minutes * 60_000
    payload = {
        "start": end - minutes * 60_000,
        "end": end,
        "requestType": request_type,
        "variables": {},
        "compositeQuery": {"queries": queries},
    }
    resp = httpx.post(
        base_url() + QUERY_RANGE_PATH, json=payload, headers=_headers(), timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def _builder(name: str, spec: dict, signal: str = "traces") -> dict:
    return {
        "type": "builder_query",
        "spec": {"name": name, "signal": signal, "disabled": False, **spec},
    }


def _rows(response: dict) -> list:
    """Extract rows from the v5 envelope: data -> data -> results[].

    raw results carry dict rows under "rows"; scalar results carry
    parallel "columns" + "data" arrays that need zipping.
    """
    node = response.get("data", response)
    while isinstance(node, dict) and isinstance(node.get("data"), dict):
        node = node["data"]
    results = node.get("results", []) if isinstance(node, dict) else []
    rows = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("columns") and isinstance(result.get("data"), list):
            names = [c.get("name") for c in result["columns"]]
            rows.extend(dict(zip(names, values)) for values in result["data"])
            continue
        for key in ("rows", "list", "series", "table"):
            value = result.get(key)
            if value:
                rows.extend(value if isinstance(value, list) else [value])
    return [_flatten(row) for row in rows]


def _flatten(row):
    """Raw rows nest span fields under "data"; merge them to the top level."""
    if isinstance(row, dict) and isinstance(row.get("data"), dict):
        merged = {k: v for k, v in row.items() if k != "data"}
        merged.update(row["data"])
        return merged
    return row


def get_slow_spans(service: str | None = None, minutes: int = 30, limit: int = 15) -> list:
    spec = {
        "selectFields": SPAN_FIELDS,
        "order": [{"key": {"name": "duration_nano"}, "direction": "desc"}],
        "limit": limit,
    }
    if service:
        spec["filter"] = {"expression": f"service.name = '{service}'"}
    return _rows(query_range("raw", [_builder("slow_spans", spec)], minutes))


def get_error_spans(service: str | None = None, minutes: int = 30, limit: int = 15) -> list:
    expression = "has_error = true"
    if service:
        expression += f" AND service.name = '{service}'"
    spec = {
        "filter": {"expression": expression},
        "selectFields": SPAN_FIELDS,
        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
        "limit": limit,
    }
    return _rows(query_range("raw", [_builder("error_spans", spec)], minutes))


def search_spans(filter_expression: str, minutes: int = 30, limit: int = 20) -> list:
    spec = {
        "filter": {"expression": filter_expression},
        "selectFields": SPAN_FIELDS,
        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
        "limit": limit,
    }
    return _rows(query_range("raw", [_builder("search", spec)], minutes))


def get_service_stats(minutes: int = 30) -> list:
    """Per-service span count, p99 latency (ns) and error count."""
    totals = _builder(
        "totals",
        {
            "stepInterval": 60,
            "aggregations": [
                {"expression": "count()", "alias": "span_count"},
                {"expression": "p99(duration_nano)", "alias": "p99_duration_nano"},
            ],
            "groupBy": [{"name": "service.name", "fieldContext": "resource"}],
        },
    )
    errors = _builder(
        "errors",
        {
            "stepInterval": 60,
            "aggregations": [{"expression": "count()", "alias": "error_count"}],
            "filter": {"expression": "has_error = true"},
            "groupBy": [{"name": "service.name", "fieldContext": "resource"}],
        },
    )
    stats: dict = {}
    # Scalar columns come back as __result_N in aggregation order, not by alias.
    for row in _rows(query_range("scalar", [totals], minutes)):
        svc = row.get("service.name")
        stats[svc] = {
            "service.name": svc,
            "span_count": row.get("__result_0"),
            "p99_duration_nano": row.get("__result_1"),
            "error_count": 0,
        }
    for row in _rows(query_range("scalar", [errors], minutes)):
        svc = row.get("service.name")
        entry = stats.setdefault(svc, {"service.name": svc, "span_count": None,
                                       "p99_duration_nano": None, "error_count": 0})
        entry["error_count"] = row.get("__result_0", 0)
    return list(stats.values())


def get_logs(filter_expression: str | None = None, minutes: int = 30, limit: int = 20) -> list:
    """Raw log lines (body, severity, service, trace_id), newest first."""
    spec = {
        "order": [{"key": {"name": "timestamp"}, "direction": "desc"}],
        "limit": limit,
    }
    if filter_expression:
        spec["filter"] = {"expression": filter_expression}
    return _rows(query_range("raw", [_builder("logs", spec, signal="logs")], minutes))


def compare_windows(minutes: int = 30) -> dict:
    """Per-service stats for the last `minutes` vs the window before it."""
    return {
        "current_window": get_service_stats(minutes=minutes),
        "previous_window": _service_stats_offset(minutes),
    }


def _service_stats_offset(minutes: int) -> list:
    totals = _builder(
        "totals",
        {
            "stepInterval": 60,
            "aggregations": [
                {"expression": "count()", "alias": "span_count"},
                {"expression": "p99(duration_nano)", "alias": "p99_duration_nano"},
            ],
            "groupBy": [{"name": "service.name", "fieldContext": "resource"}],
        },
    )
    errors = _builder(
        "errors",
        {
            "stepInterval": 60,
            "aggregations": [{"expression": "count()", "alias": "error_count"}],
            "filter": {"expression": "has_error = true"},
            "groupBy": [{"name": "service.name", "fieldContext": "resource"}],
        },
    )
    stats: dict = {}
    for row in _rows(query_range("scalar", [totals], minutes, end_offset_minutes=minutes)):
        svc = row.get("service.name")
        stats[svc] = {
            "service.name": svc,
            "span_count": row.get("__result_0"),
            "p99_duration_nano": row.get("__result_1"),
            "error_count": 0,
        }
    for row in _rows(query_range("scalar", [errors], minutes, end_offset_minutes=minutes)):
        svc = row.get("service.name")
        entry = stats.setdefault(svc, {"service.name": svc, "span_count": None,
                                       "p99_duration_nano": None, "error_count": 0})
        entry["error_count"] = row.get("__result_0", 0)
    return list(stats.values())


def trace_url(trace_id: str) -> str:
    return f"{base_url()}/trace/{trace_id}"
