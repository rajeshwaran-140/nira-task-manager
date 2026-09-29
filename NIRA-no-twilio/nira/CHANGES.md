Voice assistant backend - files in this zip (unzip over your project; paths are the project's real layout)

NEW      app/assistant.py              service: prompt, tools, validation, pending actions, confirm/cancel
NEW      app/routers/assistant.py      POST /assistant/command, /confirm/{id}, /cancel/{id}
NEW      tests/test_assistant.py       mocked-provider tests
CHANGED  app/ai_providers.py           tool calling for Gemini + OpenAI (complete()/ping() unchanged)
CHANGED  app/models.py                 + PendingAction, AssistantTurn (+ User relationships)
CHANGED  app/schemas.py                + Assistant* schemas
CHANGED  app/errors.py                 + action_expired, action_not_pending
CHANGED  app/main.py                   registers the router
CHANGED  tests/conftest.py             + CHAT_RATE_LIMIT=1000/minute for the test run
CHANGED  README.md, .env.example       docs
Tables are created by Base.metadata.create_all on start-up (no migration needed while you use create_all).

Voice screen (frontend only; the backend is unchanged)
CHANGED  static/index.html   + "Voice" tab: mic button (tap to listen, tap to stop, pulsing while listening, live words),
                               SpeechRecognition (ta-IN / en-IN from Settings > Language), text box fallback, "Try saying..." hint,
                               confirmation card (Confirm / Cancel, or say yes / no), speech replies with a Voice on/off switch on the Voice tab,
                               Settings button when the AI key is missing, clear mic / no-speech / network messages. Text is escaped, never raw innerHTML.
CHANGED  static/sw.js        cache v2; only the app shell ("/" page, icon, manifest) is cached. Before, opening any API URL as a page
                               (for example /docs) was saved over the cached app page. API calls are never cached.

Scheduler + Twilio phone calls
NEW      app/calls.py                  E.164 check, Twilio Voice client (httpx), call messages, config check
NEW      app/scheduler.py              due-reminder detection (existing lead_days/call_time/consent), atomic claim, APScheduler start/stop
NEW      app/routers/calls.py          POST /calls/test (login, owner-only, rate-limited)
NEW      tests/test_calls.py           mocked-Twilio tests (no network)
CHANGED  app/config.py                 + twilio_phone_number, nira_user_phone_number, scheduler_*, test_call_rate_limit
CHANGED  app/errors.py                 + twilio_not_configured, call_forbidden, call_failed (English + Tamil)
CHANGED  app/ratelimit.py              + test_call_limit
CHANGED  app/main.py                   registers router; starts/stops the scheduler in lifespan
CHANGED  static/index.html             + "Make Test Call" link in the Proactive calls card (nothing else)
CHANGED  tests/conftest.py             + test env (scheduler off, Twilio empty) and an autouse guard blocking the real Twilio client
CHANGED  .env.example, render.yaml, README.md, CHANGES.md

4B: Twilio call results, retry and call history
NEW      app/call_results.py           status -> call row + reminder updates (forward-only, atomic), stale-call check with Twilio
NEW      tests/test_call_results.py     retries, limits, duplicates, webhook signature, history (fake Twilio)
CHANGED  app/scheduler.py              a claim now records an ATTEMPT only; retries; results decide success (fixes 4A)
CHANGED  app/calls.py                  status callback URL + signature check, StatusCallback options, fetch_call
CHANGED  app/routers/calls.py          + POST /twilio/status (signed webhook), GET /calls/history; test call is logged
CHANGED  app/routers/deadlines.py      `called` = answered call (last_call_at), not merely attempted
CHANGED  app/assistant.py              comment only
CHANGED  app/models.py                 comments only (no schema change)
CHANGED  app/schemas.py, app/config.py + CallHistoryOut; call_max_retries, call_retry_delay_minutes, twilio_status_callback_url
CHANGED  static/index.html             Calls tab shows real call history (previews kept, listed separately)
CHANGED  tests/test_calls.py           fake gains fetch_call; 4A tests updated where they encoded the old "never retry" behaviour
CHANGED  .env.example, README.md, CHANGES.md

4B FINALIZATION (review pass; no new features)
CHANGED  app/calls.py                  signature check compares bytes (a non-ASCII X-Twilio-Signature was a 500, now a 403); module note updated (it still said "no callbacks or retries"); stray blank lines
CHANGED  app/routers/calls.py          webhook refuses oversized bodies by declared size and while streaming (never buffers more than 20 KB); CallDuration parsed strictly
CHANGED  tests/conftest.py             pins APP_BASE_URL, TWILIO_STATUS_CALLBACK_URL, CALL_MAX_RETRIES, CALL_RETRY_DELAY_MINUTES so a developer's .env cannot break 4B tests
CHANGED  tests/test_call_results.py    + odd CallDuration test, + non-ASCII/empty signature test
CHANGED  README.md, CHANGES.md, .env.example   4B setup guide: env vars, callbacks, retries, single-number limit, local tunnel, running, tests, limitations
Reviewed with no change needed: call_results.py, scheduler.py, routers/deadlines.py, models.py, config.py, schemas.py, main.py, static/index.html (Calls tab already shows title, masked phone, time, status, retries).

5A DEPLOYMENT (Render + PostgreSQL + Twilio; no new features)
CHANGED  app/config.py        prod check: SMS_PROVIDER must be "twilio" with SID/token/TWILIO_FROM (an empty value used to pass and break OTP login); APP_BASE_URL must be https (magic links and Twilio callbacks are built from it)
CHANGED  app/main.py          /docs, /redoc, /openapi.json are switched off when APP_ENV=prod
CHANGED  render.yaml          explicit plans (web "starter" = always on, so the scheduler runs; database "basic-256mb"), region, database not reachable from the internet (ipAllowList: []), SMS_PROVIDER=twilio, + TWILIO_FROM, + SCHEDULER_ENABLED, single-process note
NEW      tests/test_deploy.py production start-up check tests (settings objects only)
CHANGED  README.md            Render + PostgreSQL + Twilio deployment guide, env var table, checks, limitations; stale ".env is already created" line fixed
CHANGED  .env.example         prod notes
Checked, no change needed: PostgreSQL URL handling (postgres:// and postgresql:// -> psycopg2) and pool_pre_ping; every column length matches the input validation (Postgres enforces lengths, SQLite does not); frontend uses same-origin relative URLs and https-safe service worker (only the app shell is cached); webhook signature uses the configured public URL, so it is correct behind Render's proxy; uvicorn runs with --proxy-headers so rate limits see the real client IP.
