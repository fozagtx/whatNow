# 90-second demo script

One flow. No feature tour. Screen recording: browser with two tabs -
the app (localhost:8000) and SigNoz (localhost:8080).

**0:00–0:15, the problem (talk over the SigNoz dashboard tab)**

> "When production breaks, the evidence is already in your observability
> stack. But finding it means dashboards, query builders, and someone senior
> losing an hour at 2am. This is a real system, a ride-hailing app -
> streaming live traces into SigNoz right now. Nothing mocked, nothing
> seeded."

**0:15–0:45, the ask (switch to the app tab)**

Type: `why are ride dispatches slow and are there any errors?`

> "One plain-English question. Watch the bottom of the answer, those are
> live SigNoz queries the agent is choosing itself: service stats first,
> then it drills into the suspects, then into one specific trace."

**0:45–1:05, the answer**

> "Root cause: dispatch spends 1.2 of its 1.6 seconds inside a single MySQL
> SELECT. And it caught a second issue I didn't ask about, the redis driver
> lookups are erroring and retrying. Suggested fix, confidence, and every
> claim has a trace attached."

**1:05–1:25, the proof (click "view trace ↗")**

> "This is the part that makes it trustworthy: the exact trace in SigNoz.
> There's the 1.19-second SQL SELECT, there are the red error spans -
> exactly what the agent said. It doesn't ask you to believe it; it shows
> its work."

**1:25–1:30, close**

> "Root cause, evidence, and a fix, in 30 seconds, in plain English, for
> anyone on the team. Built on SigNoz's query API with an agent that shows
> every query it ran."

## Recording checklist

- [ ] `docker start hotrod   # runs on :8085` + fire ~24 requests so the last 30 min have data:
      `for i in 1 2 3; do for c in 123 392 731 567; do curl -s -o /dev/null "http://localhost:8085/dispatch?customer=$c&nonse=$i$c" & done; wait; done`
- [ ] App running: `.venv/bin/uvicorn app.main:app --port 8000`
- [ ] Logged into SigNoz in the same browser (trace links open instantly)
- [ ] Do one warm-up question off-camera first (first answer can take ~30s)
