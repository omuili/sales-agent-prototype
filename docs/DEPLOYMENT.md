# Production Deployment Plan

## Target architecture

```
Users (SSO) ──► Load balancer / HTTPS ──► Streamlit app containers (autoscaled, stateless)
                                               │
                     ┌─────────────────────────┼──────────────────────────────┐
                     ▼                         ▼                              ▼
             Job queue (Redis/SQS) ──► Research workers         Postgres (watches, seen URLs,
                                       (pipeline runs)          saved briefs, audit log)
                                               │
                     Redis cache (search/pages/10-K)    Secrets manager (OpenAI, Tavily, SMTP keys)
                                               │
        Scheduler (Cloud Scheduler / cron) ──► alert job ──► email service (SES / SendGrid)
```

The prototype runs everything in one process. In production I'd split it into three parts:

1. **Web tier.** The Streamlit container is stateless. It enqueues a research job and polls for the result, so a
   2-minute run doesn't hold a web worker or fail when the browser reconnects.
2. **Worker tier.** Runs `run_pipeline` from the queue. It scales on queue depth, independently of web traffic.
3. **Scheduled alert job.** `cap-931-alerts` runs on a schedule (e.g. hourly) as a separate job.

**Hosting:** Docker images on Google Cloud Run, AWS ECS Fargate or Azure Container Apps. All three scale to zero
when idle and can hold several instances under load. Streamlit Community Cloud is fine for the demo, but it lacks
authentication and background workers.

## Scalability

- **Most of the time goes to network and LLM calls, not CPU.**
  - Agents and page fetches already run in parallel threads.
  - Workers scale horizontally.
- **Shared Redis cache** instead of the local disk cache, so all workers reuse search, page and 10-K results.
  Popular accounts are researched once a day, not once per rep.
- **Rate limits.**
  - A per-provider token bucket covers OpenAI, Tavily and SEC (SEC allows 10 requests/s).
  - Exponential backoff is already in the OpenAI client (`max_retries=3`).
  - Use a paid search API (Tavily or similar) in production. DuckDuckGo's unofficial endpoint isn't meant for volume.
- **LLM cost control.**
  - The fast model does the high-token extraction work.
  - Per-user daily run quotas.
  - Usage is logged per run (already captured in `OnePager.usage`).
  - Prompt caching on the shared system prompt prefix.

## Security

- **Secrets** (OpenAI, Tavily, SMTP) live in the cloud secrets manager and are injected as environment variables.
  Never in the image or repo (`.env` is gitignored).
- **Authentication.**
  - Put the app behind company SSO (OIDC via the load balancer or an auth proxy), so only reps can use it.
  - Alerts are tied to the signed-in user's email, not a free-text address.
- **Outbound fetching (SSRF).**
  - The app fetches user-supplied URLs, so in production block private IP ranges and metadata endpoints
    (169.254.169.254).
  - Cap response sizes, and allow only http/https.
  - Run workers in a network segment with no access to internal systems.
- **Prompt injection.**
  - Scraped text is wrapped in `<evidence>` tags and the system prompt marks it untrusted.
  - The verifier rejects claims the sources don't support.
  - No agent has tools that act on the outside world.
- **Data handling.**
  - Only public data is collected.
  - Uploaded decks are processed in memory and not stored.
  - Use an OpenAI organization with zero data retention for confidential decks.
- **Respect for sources.** Honor robots.txt, identify the client to SEC as required, and keep request rates
  modest.

## Maintenance and operations

- **CI** (GitHub Actions):
  - `uv sync --locked`, `pytest` and a container build on every pull request.
  - Deploy to staging, then production on approval.
  - `uv.lock` pins dependencies, and Dependabot proposes updates.
- **Monitoring.**
  - Structured logs per run, with the run id, timings per stage, token usage per model and the number of sources.
  - Alerts on error rate, p95 latency and daily LLM spend.
- **Quality monitoring.**
  - Track the verifier's unsupported-claim rate over time. A rising rate is an early sign that a prompt or model
    change made things worse.
  - Re-run `experiments/run_experiments.py` before changing models or prompts.
- **Model lifecycle.**
  - Model names are configuration, not code.
  - When OpenAI retires a model, switch the environment variable, re-run the experiments and compare.
- **Scraper drift.**
  - Site markup changes over time. Extraction uses trafilatura, not site-specific selectors, and falls back to
    search snippets.
  - A weekly smoke test on a few known companies catches regressions.
- **Data retention.** Cached research expires after 24h. Saved briefs and alert history follow the company's
  retention policy.
