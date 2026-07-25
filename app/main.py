"""Block 3: FastAPI app — chat endpoint, alert webhook, and the single-page UI."""

import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import signoz
from app.agent import analyze

app = FastAPI(title="Why Did It Break?")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

INCIDENTS: list[dict] = []
MAX_INCIDENTS = 20

# Re-investigating the same firing alert on every evaluator tick burns
# LLM quota with zero new information. One investigation per alert per window.
INVESTIGATION_COOLDOWN_SECONDS = 1800
_LAST_INVESTIGATED: dict[str, float] = {}


class Ask(BaseModel):
    question: str


def _link_evidence(answer: dict) -> dict:
    for item in answer.get("evidence", []):
        if item.get("trace_id"):
            item["trace_url"] = signoz.trace_url(item["trace_id"])
    return answer


@app.get("/")
def index() -> HTMLResponse:
    with open(os.path.join(STATIC_DIR, "index.html")) as fh:
        html = fh.read().replace("__SIGNOZ_URL__", signoz.base_url())
    return HTMLResponse(html)


def _signoz_unreachable() -> str | None:
    """Preflight so an unreachable SigNoz fails fast without spending LLM credit."""
    try:
        resp = httpx.get(signoz.base_url() + "/api/v1/health", timeout=3)
        if resp.status_code == 200:
            return None
        status = f"responded HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        status = f"is unreachable ({type(exc).__name__})"
    return (
        f"SigNoz at {signoz.base_url()} {status}. This deployment is not "
        "connected to a live SigNoz instance. Run the app next to your "
        "SigNoz, or set SIGNOZ_URL to a reachable one. No AI credits were spent."
    )


@app.post("/api/ask")
async def ask(body: Ask):
    if problem := _signoz_unreachable():
        return JSONResponse(status_code=503, content={"error": problem})
    try:
        report, trail = await analyze(body.question)
    except Exception as exc:  # noqa: BLE001 — surface config/upstream failures readably
        return JSONResponse(status_code=502, content={"error": _friendly_llm_error(exc)})
    return {"answer": _link_evidence(report.model_dump()), "investigation": trail}


def _friendly_llm_error(exc: Exception) -> str:
    text = str(exc)
    if "Invalid response from" in text or "429" in text or "rate" in text.lower():
        return ("The free AI model is rate-limited right now. Wait a minute "
                "and ask again. No data was lost.")
    return f"{type(exc).__name__}: {text[:300]}"


@app.post("/api/alert")
async def alert_webhook(request: Request, background: BackgroundTasks):
    """SigNoz notification-channel webhook: fire-and-forget auto-RCA."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    background.add_task(_investigate_alert, payload)
    return JSONResponse(status_code=202, content={"status": "investigating"})


@app.get("/api/incidents")
def incidents() -> list[dict]:
    return INCIDENTS


def _alert_summary(payload: dict) -> str:
    """Tolerant extraction across SigNoz / alertmanager-style payloads."""
    alerts = payload.get("alerts") or [payload]
    first = alerts[0] if isinstance(alerts, list) and alerts else {}
    labels = first.get("labels", {}) if isinstance(first, dict) else {}
    annotations = first.get("annotations", {}) if isinstance(first, dict) else {}
    name = labels.get("alertname") or payload.get("title") or "unnamed alert"
    detail = (annotations.get("summary") or annotations.get("description")
              or payload.get("description") or "")
    severity = labels.get("severity", "")
    parts = [name]
    if severity:
        parts.append(f"severity={severity}")
    if detail:
        parts.append(detail)
    return ". ".join(parts)


def _alert_status(payload: dict) -> str:
    alerts = payload.get("alerts") or [payload]
    first = alerts[0] if isinstance(alerts, list) and alerts else {}
    return (first.get("status") or payload.get("status") or "firing").lower()


async def _investigate_alert(payload: dict) -> None:
    summary = _alert_summary(payload)
    if _alert_status(payload) == "resolved":
        return  # nothing to root-cause when the alert clears
    now = time.monotonic()
    last = _LAST_INVESTIGATED.get(summary)
    if last is not None and now - last < INVESTIGATION_COOLDOWN_SECONDS:
        return
    _LAST_INVESTIGATED[summary] = now
    if problem := _signoz_unreachable():
        INCIDENTS.insert(0, {
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "alert": summary,
            "answer": {"root_cause": problem, "evidence": [],
                       "suggested_fix": "", "confidence": "low"},
            "investigation": [],
        })
        del INCIDENTS[MAX_INCIDENTS:]
        return
    question = (
        f"An alert just fired: {summary}. Find the root cause and what to do about it."
    )
    try:
        report, trail = await analyze(question)
        answer = _link_evidence(report.model_dump())
    except Exception as exc:  # noqa: BLE001 — a failed investigation is still an incident entry
        answer = {"root_cause": f"Auto-investigation failed: {exc}", "evidence": [],
                  "suggested_fix": "", "confidence": "low"}
        trail = []
    incident = {
        "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "alert": summary,
        "answer": answer,
        "investigation": trail,
    }
    INCIDENTS.insert(0, incident)
    del INCIDENTS[MAX_INCIDENTS:]
    _post_slack(incident)


def _post_slack(incident: dict) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return
    answer = incident["answer"]
    lines = [
        f":rotating_light: *{incident['alert']}*",
        f"*Root cause:* {answer.get('root_cause', '?')}",
        f"*Fix:* {answer.get('suggested_fix', '?')}",
        f"*Confidence:* {answer.get('confidence', '?')}",
    ]
    lines += [
        f"• {e.get('service')}: {e.get('detail')} · {e.get('trace_url', '')}"
        for e in answer.get("evidence", [])
    ]
    try:
        httpx.post(url, json={"text": "\n".join(lines)}, timeout=10)
    except httpx.HTTPError:
        pass  # Slack being down must not lose the incident record
