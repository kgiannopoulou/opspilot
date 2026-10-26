# Resilience: what happens when things fail

OpsPilot is used *during* outages, so it must keep working when parts of its own stack are failing. The rule is
**degrade, don't break**: losing Redis, the LLM or the knowledge base costs speed or polish, never the ability to work
an incident. Losing PostgreSQL — the system of record — is answered with a clear, retryable error, and queued AI work
is kept rather than dropped.

Every row below is a test in [`backend/tests/test_failures.py`](../backend/tests/test_failures.py) (or the file named),
run in CI on every push.

## Failure matrix

| Failure | Behaviour | What the user sees | Test |
|---|---|---|---|
| **PostgreSQL unavailable** | Every request that needs the database returns `503 {"error": "database_unavailable"}` with `Retry-After: 5`; no stack trace, no driver details | "The database is unavailable. Try again shortly." | `test_database_down_is_a_clear_retryable_503` |
| PostgreSQL down while the worker runs | The worker does **not** acknowledge the message and backs off 5 s; once the database is back the pending message is reclaimed and the job runs exactly once | Triage / investigation arrive late, not never | `test_worker_keeps_jobs_while_the_database_is_down` |
| **Redis unavailable** | Login throttle fails open (bcrypt still slows guessing); dashboard cache computes directly; jobs run inside the API after the response (`BackgroundTasks`); each Redis call costs at most one 0.5 s timeout (no client retries) | Everything works; AI results take a little longer; incident page shows "AI running in degraded mode — the job queue is unavailable" | `test_everything_still_works_without_redis`, `test_login_with_redis_down_is_still_checked`, `test_workers.py::test_without_redis_jobs_run_in_process` |
| **LLM API timeout** | The writer falls back to the extractive write-up; the error is stored in `grounding.llm_error` | The investigation completes; the panel says "LLM unavailable (…) — extractive write-up shown" | `test_llm_timeout_falls_back_to_the_extractive_writer` |
| LLM keeps failing (outage) | **Circuit breaker**: after 3 consecutive failures calls fail fast for 60 s (no waiting for timeouts), then one trial call (half-open) decides whether to close | Investigations stay fast; banner "The LLM provider is failing: write-ups and triage use the deterministic engines until it recovers" | `test_circuit_breaker_stops_calling_a_failing_provider` |
| **LLM returns malformed JSON** | Truncated JSON, prose instead of JSON, no text block, `max_tokens`, refusal → `LLMError` → deterministic path | Same as a timeout | `test_malformed_model_output_is_an_llm_error` |
| LLM returns *valid* JSON that is wrong | The grounding check rejects drafts that cite unknown evidence or numbers not in the evidence; the extractive write-up is shown and the rejected draft's report stored | "LLM draft rejected (1 unknown refs) — extractive write-up shown" | `test_schema_valid_but_wrong_draft_is_rejected`, `test_classification.py::test_llm_classifier_is_constrained_to_the_catalogue` |
| **Vector search returns nothing** | Investigations complete from logs, metrics, changes and history; no document evidence; grounding still passes; semantic search returns an empty list | "No matching documents." | `test_investigation_without_any_knowledge_base` |
| **Worker crashes** mid-job | The message stays pending; another worker takes it over with `XAUTOCLAIM` after 60 s idle; finished jobs are never re-run | The job completes on another worker | `test_a_crashed_workers_job_is_taken_over`, `test_workers.py::test_finished_jobs_are_not_run_twice` |
| A job keeps failing | 3 attempts with exponential backoff, then the dead-letter stream; the job row keeps the error | Admin → Background jobs shows `DEAD` with the error | `test_workers.py::test_job_out_of_attempts_goes_to_the_dead_letter_stream` |
| The pipeline fails for good | Investigation `FAILED` with the stage that failed | "AI investigation temporarily unavailable. Manual investigation tools remain accessible…" with a retry button | `test_workers.py` (dead-letter), UI in `incidents/[number]/page.tsx` |
| **Duplicate ticket** (retry / double submit) | `Idempotency-Key` header: the same key returns the original ticket (`200`, `Idempotent-Replayed: true`) and triage is queued once; the same key with a different body is `422`; concurrent requests with one key create one ticket (unique constraint decides) | One ticket | `test_idempotency_key_makes_retries_safe`, `test_concurrent_requests_with_one_key_create_one_ticket` |
| Duplicate ticket (same report, no key) | The same reporter submitting the same normalised title/description/service within 10 minutes while it is open gets `409` with `existing`; `allow_duplicate=true` overrides; another person reporting the same thing is *not* a duplicate | "You reported the same thing a few minutes ago: TCK-… · Create anyway" | `test_the_same_report_twice_is_flagged_as_a_duplicate` |
| Two investigations at once | One active investigation per incident; a second request is `409` | "investigation N is already in progress" | `test_investigations.py` |

## Knowing the current state

`GET /api/v1/ai/status` reports the LLM circuit (`closed` / `open` / `half_open`, consecutive failures, last error),
whether the job queue is reachable and which embedding model is active, plus a `degraded` flag and a human message.
The incident page polls it and shows the banner above; Step 22 exposes the same signals as Prometheus metrics.

## Deliberate limits

- Circuit-breaker state is per process: the API and each worker decide on their own. A shared breaker in Redis would
  fail exactly when Redis does.
- Running jobs inside the API when Redis is down trades throughput for availability; under real load the queue
  should be restored first (alerting on `/ready`).
- Duplicate detection is exact (normalised text), not semantic: "checkout broken" and "can't pay" from the same
  person are two tickets. The triage correlation links both to the same incident instead.
