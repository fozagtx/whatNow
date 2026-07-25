"""Block 2: the RCA agent.

The model drives the investigation: it decides which SigNoz queries to run,
follows leads across services, and must ground its answer in span evidence.
Built on Pydantic AI with OpenRouter as the model gateway.
"""

import os
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_FALLBACKS = "nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free"

from app import signoz


class Evidence(BaseModel):
    service: str
    span: str
    trace_id: str = ""
    detail: str = Field(description="The latency or error actually observed, in human units (ms/s)")


class RCAReport(BaseModel):
    root_cause: str = Field(description="One or two sentences naming the failing service/operation and why")
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)
    suggested_fix: str = Field(description="One or two concrete, actionable sentences")
    confidence: Literal["high", "medium", "low"]


INSTRUCTIONS = """You are a senior SRE investigating incidents in a system
observed by SigNoz. Answer the user's question by querying the live telemetry
with your tools — never from prior knowledge.

Investigation approach:
1. Start with get_service_stats to see which services are slow or erroring.
   For "what changed / got worse?" questions, use compare_windows instead.
2. Drill into suspects with get_slow_spans / get_error_spans.
3. Use search_spans with a filter expression to follow a specific lead
   (a trace_id, a service, a duration threshold).
4. When you have a suspect trace_id or an erroring service, check get_logs
   ONCE for the actual error message. If it reports no logs, the system does
   not ship logs — never retry get_logs; trace evidence is sufficient.
5. Stop as soon as the evidence supports a conclusion — usually 2-5 tool calls.

Rules:
- Ground every claim in rows returned by your tools. Never invent services,
  spans, or numbers.
- duration_nano is nanoseconds — convert to ms/s in anything you write.
- ALWAYS return 1-3 evidence entries when your tools returned any rows — the
  UI renders each entry as a clickable trace link, so put the trace_id in the
  evidence entry (not only in prose). An answer without evidence entries is
  only acceptable when the telemetry was empty or unreachable.
- If a tool returns an error or no rows, you may adapt ONCE (widen the
  window, drop a filter). If two calls in a row fail with the same kind of
  error (e.g. connection refused), STOP: report that the telemetry backend
  is unreachable in root_cause and set confidence to "low".
- If the telemetry genuinely cannot answer the question, say so in root_cause
  and set confidence to "low".
"""


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    slugs = [os.getenv("LLM_MODEL", DEFAULT_MODEL)]
    slugs += [s.strip() for s in
              os.getenv("LLM_FALLBACK_MODELS", DEFAULT_FALLBACKS).split(",")
              if s.strip() and s.strip() not in slugs]
    models = [OpenAIChatModel(s, provider=OpenRouterProvider()) for s in slugs]
    # Free-tier models rate-limit or return unparseable blobs under load;
    # fall through the chain instead of surfacing that to the user.
    model = FallbackModel(*models,
                          fallback_on=(ModelHTTPError, UnexpectedModelBehavior))
    agent = Agent(model, output_type=RCAReport, instructions=INSTRUCTIONS,
                  retries=2, model_settings={"max_tokens": 4096})

    @agent.tool_plain
    def get_service_stats(minutes: int = 30) -> list | dict:
        """Per-service span count, p99 latency (nanoseconds) and error count
        over the last `minutes`. The right first call for almost any question."""
        return _guarded(signoz.get_service_stats, minutes=minutes)

    @agent.tool_plain
    def get_slow_spans(service: str | None = None, minutes: int = 30, limit: int = 15) -> list | dict:
        """Slowest raw spans, ordered by duration_nano descending.
        Optionally restricted to one service."""
        return _guarded(signoz.get_slow_spans, service=service, minutes=minutes, limit=limit)

    @agent.tool_plain
    def get_error_spans(service: str | None = None, minutes: int = 30, limit: int = 15) -> list | dict:
        """Most recent spans with has_error = true, optionally for one service.
        Rows include status_message when the instrumentation set one."""
        return _guarded(signoz.get_error_spans, service=service, minutes=minutes, limit=limit)

    @agent.tool_plain
    def get_logs(filter_expression: str | None = None, minutes: int = 30, limit: int = 20) -> list | dict:
        """Raw log lines (body, severity_text, service.name, trace_id), newest
        first. Read the actual error text once you have a suspect, e.g.
        "trace_id = 'abc123'" or "severity_text IN ('ERROR','FATAL')".
        If it reports no logs, do not call it again in this investigation."""
        result = _guarded(signoz.get_logs, filter_expression=filter_expression,
                          minutes=minutes, limit=limit)
        if result == []:
            return {
                "no_logs": "No log rows matched. This system ships no logs to "
                           "SigNoz. Do NOT call get_logs again — conclude from "
                           "trace evidence, and note the absence of logs only if relevant."
            }
        return result

    @agent.tool_plain
    def compare_windows(minutes: int = 30) -> dict:
        """Per-service span_count / p99_duration_nano / error_count for the
        last `minutes` vs the equal-length window before it. The right tool
        for "what changed?", "did it get worse?", and regression hunting."""
        return _guarded(signoz.compare_windows, minutes=minutes)

    @agent.tool_plain
    def search_spans(filter_expression: str, minutes: int = 30, limit: int = 20) -> list | dict:
        """Raw span search with a SigNoz filter expression. Examples:
        "trace_id = 'abc123'"
        "service.name = 'payment' AND duration_nano > 1000000000"
        "has_error = true AND name CONTAINS 'charge'"
        """
        return _guarded(signoz.search_spans, filter_expression=filter_expression,
                        minutes=minutes, limit=limit)

    return agent


def _guarded(fn, **kwargs):
    """Return query failures to the model as data so it can adapt its plan."""
    try:
        return fn(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


async def analyze(question: str) -> tuple[RCAReport, list[dict]]:
    """Run the agent; return the report plus the tool-call trail for the UI."""
    result = await get_agent().run(
        question, usage_limits=UsageLimits(request_limit=16)
    )
    return _strip_em_dashes(_backfill_evidence(result.output)), _investigation_trail(result)


def _strip_em_dashes(report: RCAReport) -> RCAReport:
    """House style: no em dashes in anything the user reads."""
    report.root_cause = report.root_cause.replace(" — ", ", ").replace("—", "-")
    report.suggested_fix = report.suggested_fix.replace(" — ", ", ").replace("—", "-")
    for item in report.evidence:
        item.detail = item.detail.replace(" — ", ", ").replace("—", "-")
    return report


def _backfill_evidence(report: RCAReport) -> RCAReport:
    """Some models cite trace ids in prose but leave the evidence array empty.

    Recover those ids so the UI always renders clickable trace proof."""
    if report.evidence:
        return report
    for trace_id in dict.fromkeys(re.findall(r"\b[0-9a-f]{32}\b", report.root_cause)):
        report.evidence.append(Evidence(
            service="(cited in root cause)", span="trace", trace_id=trace_id,
            detail="Trace referenced by the investigation",
        ))
    return report


def _investigation_trail(result) -> list[dict]:
    trail = []
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if (getattr(part, "part_kind", "") == "tool-call"
                    and part.tool_name != "final_result"):
                args = part.args_as_dict() if hasattr(part, "args_as_dict") else part.args
                trail.append({"tool": part.tool_name, "args": args})
    return trail
