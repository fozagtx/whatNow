# Why Did It Break?

Ask your app what happened to it: an agent investigates your live SigNoz
telemetry and returns the root cause, the evidence traces, and a fix, from a
plain-English question or straight from a firing alert.

## What is it?

Why Did It Break adds an investigation agent on top of a SigNoz instance:

- **The RCA agent** plans its own investigation with six telemetry tools and
  returns a typed report (root cause, evidence, fix, confidence). It is
  required.
- **The chat UI** is a single page for asking questions by hand. It is
  optional; the API works without it.
- **The alert webhook** turns SigNoz alerts into automatic investigations -
  the alert is the question. It is optional.
- **The Slack notifier** posts each auto-investigation to a channel. It is
  optional; when omitted, incidents are still stored and shown in the UI.

Every claim in an answer links to the exact trace in SigNoz that proves it,
and every answer shows the queries the agent chose to run.

## How it works

```text
  your question                     SigNoz alert (webhook)
        |                                   |
        v                                   v
   +-----------------------------------------------+
   |                  RCA agent                     |
   |   loops, choosing tools per step:              |
   |     get_service_stats     per-service p99/err  |
   |     compare_windows       now vs. before       |
   |     get_slow_spans        slowest raw spans    |
   |     get_error_spans       recent failures      |
   |     search_spans          free-form drilldown  |
   |     get_logs              error text by trace  |
   +-----------------------------------------------+
        |  every call = live POST /api/v5/query_range
        v
   { root_cause, evidence[] -> trace links, fix, confidence }
        |
        +--> chat UI answer card + investigation trail
        +--> incident feed (and Slack, if configured)
```

The loop stops when the evidence supports a conclusion, usually 3–7 queries.
Safety limits: a hard cap of 12 model requests per investigation, a
retry-once rule for empty results, and a no-logs sentinel so a system that
ships no logs cannot send the agent fishing. If the telemetry cannot answer
the question, the agent says so and marks confidence low instead of
inventing an answer.

## Why use it?

- Root cause in seconds instead of dashboard archaeology at 2am.
- Anyone on the team can ask, support, PMs, the engineer who didn't build
  this service, not just whoever knows the query builder.
- Answers are auditable: trace links plus the full query trail, so you can
  verify rather than trust.
- Alerts investigate themselves before the on-call opens a laptop.

Results depend on what your services actually export to SigNoz and on the
model behind the agent. Query counts and answer times are typical figures,
not guarantees.

## Install

Requires Python 3.10+, Docker, and a SigNoz instance.

```bash
# 1. SigNoz via Foundry (one config, one command)
curl -fsSL https://signoz.io/foundry.sh | bash
foundryctl cast -f casting.yaml

# 2. The app
pip install -r requirements.txt
cp .env.example .env   # fill in the keys below
```

Commit the generated `casting.yaml.lock` after the first cast so the
deployment is reproducible.

## Quick start

Full setup, questions plus alert auto-investigation:

```bash
uvicorn app.main:app --port 8000
# open http://localhost:8000 and ask: "why is checkout slow?"
```

Point SigNoz alerts at the agent (SigNoz → Alerts → Notification Channels →
Webhook):

```text
http://<host-running-the-app>:8000/api/alert
```

Minimal, API only, no UI, no alerts:

```bash
curl -s -X POST localhost:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"what should I fix first?"}'
```

To point the agent at your own services, instrument them with OpenTelemetry -
SigNoz's [instrumentation guide](https://signoz.io/docs/instrumentation/overview/)
covers every major language, and export to the SigNoz OTLP endpoint
(`localhost:4317` gRPC / `localhost:4318` HTTP).

No telemetry yet? Run a real workload against SigNoz, for example
HotROD, a genuinely running ride-dispatch demo app:

```bash
docker run -d --name hotrod -p 8085:8080 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318 \
  jaegertracing/example-hotrod:latest all
curl -s "http://localhost:8085/dispatch?customer=123"
```

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `SIGNOZ_URL` | yes | SigNoz base URL (API and trace links), e.g. `http://localhost:8080` |
| `SIGNOZ_API_KEY` | yes | Service-account key: SigNoz → Settings → Service Accounts → Keys |
| `OPENROUTER_API_KEY` | yes | LLM access via OpenRouter |
| `LLM_MODEL` | no | Any OpenRouter model slug; defaults to `nvidia/nemotron-3-ultra-550b-a55b:free` |
| `LLM_FALLBACK_MODELS` | no | Comma-separated slugs tried in order when the primary is rate-limited |
| `SLACK_WEBHOOK_URL` | no | Incoming webhook; when set, auto-investigations post to Slack |

## Deploy

One VPS (4 GB+ RAM, SigNoz's ClickHouse needs it): run the Foundry cast,
then this app next to it:

```bash
docker build -t whydiditbreak .
docker run -d -p 8000:8000 \
  -e SIGNOZ_URL=http://<signoz-host>:8080 \
  -e SIGNOZ_API_KEY=... -e OPENROUTER_API_KEY=... \
  whydiditbreak
```

Split hosting: SigNoz has an official
[Render guide](https://signoz.io/docs/setup/render/); the app is one
stateless container (Render/Railway/Fly) pointed at your SigNoz URL. On
Railway: deploy from GitHub (the Dockerfile is auto-detected), set the
variables above, generate a domain. `SIGNOZ_URL` must be reachable from the
host, `localhost` only works when both halves run on the same machine.

> [!WARNING]
> Every question spends LLM credit, and the SigNoz key grants read access to
> your telemetry. Put auth in front before sharing a public URL.

## What can you ask?

- Slowness: "why are ride dispatches slow?", "which requests took over 1s?"
- Errors: "are there any errors right now, and where?"
- Changes: "did anything get worse in the last 30 minutes?"
- Suspects: "is mysql the bottleneck?", "what happened in trace f44d21…?"
- Judgment: "is the system healthy?", "what should I fix first?"

Rule of thumb: if an engineer could answer it by staring at dashboards for
twenty minutes, you can ask it in one sentence.

## Important limits

- The agent reads traces and logs; a metrics tool (CPU/memory saturation) is
  not wired in yet.
- Log correlation only works if your services ship logs to SigNoz; when they
  don't, the agent says so rather than guessing.
- Incidents are stored in memory (last 20), they reset on restart.
- The v5 query_range parsing is exercised against SigNoz v0.134; other
  versions may need adjustments in `app/signoz.py`.
- No auth on the endpoints; this is a hackathon build, front it yourself.

## Roadmap

Incident memory across restarts ("same fraud-api timeout as Tuesday, here's
the fix that worked"), deploy-marker correlation via `service.version`,
a metrics tool for saturation signals, and drafting a GitHub issue from an
accepted root cause.

## AI disclosure

The agent runs on an LLM through OpenRouter (configurable via `LLM_MODEL`),
orchestrated with Pydantic AI. Every answer is grounded in telemetry queried
from SigNoz during that same run; the query trail shown with each answer is
the audit log.

## Development

```bash
python3 -m py_compile app/*.py        # syntax check
uvicorn app.main:app --reload         # dev server
```

The 90-second demo script lives in [DEMO.md](DEMO.md).
