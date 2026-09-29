# NIRA – one project (API + web app), Phase 1 + 2 + Part 3A + voice assistant + Twilio calls (4A) + call results, retry & history (4B) + Render deployment (5A)

FastAPI serves both the API and the bright new web UI (`static/index.html`), so you run **one command** and open **one URL**.

## Run in VS Code
1. Unzip, then **File → Open Folder…** → the `nira` folder.
2. Open the terminal (Ctrl+`) and run:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   python run.py
   ```
   (If VS Code asks to pick an interpreter, choose the `.venv` one.)
3. Open **http://localhost:8000**. On a phone on the same Wi-Fi, use your PC's IP instead (start with `--host 0.0.0.0` if needed).
4. Or press **F5** and pick "NIRA (run app)". "NIRA tests" runs all the tests (`python -m pytest -q`).

Create `.env` first: copy `.env.example` to `.env` (the defaults are fine for local dev). In dev, the OTP shows on screen ("Dev mode OTP") and in the terminal; magic-link emails are printed in the terminal. API docs: http://localhost:8000/docs

## What changed
* **Single project**: no separate frontend/backend folders.
* **Bright colours**: purple → pink → orange gradients, and each category has its own colour.
* **Date + time** on every deadline (time picker), shown as "3 Oct · 6:00 PM".
* **Live countdown timer** on the Home card (days / hours / minutes / seconds).
* Frontend now talks to the API: login (phone OTP, email+password, magic link), one-time phone verification for email users, add/edit/delete/done deadlines with category, repeat and notes, per-user settings, consent-gated calls switch, sign out and delete account.
* The phone number now comes from your profile (no re-entry in Settings).
* "Preview NIRA's call" is kept (Tamil/English script, now mentions the time). It's a demo only and changes nothing.

## Part 3A: your own AI key (Settings → AI)
Each user can save their own **Gemini** or **OpenAI** key. There is no chat yet; this only stores and tests the key.

**One-time setup (needed before anyone can save a key)** – generate an encryption key and paste it into `.env` as `ENCRYPTION_KEY=`:
```
python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
```
Keep it safe and do not change it later: keys saved under an old `ENCRYPTION_KEY` can't be read any more (users just save them again). If it is empty or invalid, saving returns a clear English + Tamil error.

| Endpoint (login required) | What it does |
|---|---|
| `GET /settings/ai` | `provider`, `model`, `configured`, `key_last4`, `source` (`user` / `server` / `none`), `default_models`. Never the key. |
| `PUT /settings/ai` | Save `provider`, `model` (empty = default) and `key` (trimmed; empty = keep the saved one). Changing provider needs a new key. |
| `DELETE /settings/ai` | Remove the user's key. |
| `POST /settings/ai/test` | One tiny real provider call (limit: `AI_TEST_RATE_LIMIT`, default 5/minute per IP). Errors: `invalid_key`, `quota_exceeded`, `network_error`, `timeout` (plus `model_not_found`, `provider_error`, `ai_not_configured`). |

**Fallback order** (one helper, `resolve_ai()` in `app/ai_keys.py`, used by every future AI feature): the user's own key, then the server default (`DEFAULT_AI_PROVIDER` + `DEFAULT_AI_API_KEY`), else error `ai_not_configured`.

**Safety**: keys are Fernet-encrypted in the database (only the last 4 characters are kept in clear), never returned by any endpoint, never logged, and never stored in the browser (the page reads the password box once when you tap Save). Provider error text is never passed on to the browser.

**Model names** change often. Defaults: Gemini `gemini-3.5-flash-lite`, OpenAI `gpt-6-luna`. Users can type any model name in Settings, and you can change the built-in defaults in `app/ai_providers.py` (`DEFAULT_MODELS`). Check the current names on the providers' model pages before release.

## Not built yet
Text chat agent (Part 3B), Android/push/deploy (Phase 5). Real OTP SMS needs an SMS provider in `.env` (see `.env.example`); until then, dev mode is used.

## Notes
Auth is our own JWT backend (bcrypt passwords, hashed expiring OTPs, rate limits). The session token is kept in the browser so you stay logged in. Deadline times are read as Asia/Kolkata.

## Voice assistant backend (no frontend yet)
The browser turns speech into text and sends the text; **no audio ever reaches the server**. The AI is reached through `get_ai_provider()` (own key first, then the server default).

| Endpoint (login required) | What it does |
|---|---|
| `POST /assistant/command` `{text}` | Returns `{reply_text, action, needs_more_info}`. Limited by `CHAT_RATE_LIMIT` (also used by the future chat). `action` is a **proposal** (`id`, `kind` add/update/complete/delete, `title`, `changes`, `expires_in_seconds`) or `null`. |
| `POST /assistant/confirm/{id}` | The only call that changes data. Works once, only for the owner, only before the 10-minute expiry. Returns the deadline (`null` after a delete). |
| `POST /assistant/cancel/{id}` | Drops a proposal. |

**Safety model.** The model has 5 tools: `list_deadlines` (read) and `propose_add/update/complete/delete`. A `propose_*` tool only validates and remembers a request; after the model has answered, the server stores one `pending_actions` row. `deadlines` changes only in `confirm`, which re-validates the stored fields, and claims the row with a single conditional UPDATE so two simultaneous confirms cannot both win. Someone else's action id answers `not_found`, exactly like an id that does not exist. Expired: `action_expired` (410). Confirmed or cancelled already: `action_not_pending` (409). Both have English + Tamil text.

**Validation.** Every proposal goes through `DeadlineIn` / `DeadlineUpdate` (category must be `emi, bill, exam, assign, appt, work, other`). On top of that: the date needs a time (a bare date would become midnight), impossible dates and dates in the past or more than 10 years ahead are refused. A refused call comes back to the model as a tool error so it can fix it or ask the user; nothing is stored. One change per request; ids are looked up only among the user's own deadlines.

**Conversation memory.** Table `assistant_turns` keeps the newest 6 question/answer turns of the last 10 minutes per user, so "6pm" can answer "What time on Friday?". Older rows (all users) are deleted on every command. Nothing else of the conversation is stored, and no transcript, reply, tool argument or key is ever logged.

**Prompt.** Built per request with today's date, time and weekday in the user's timezone (default `Asia/Kolkata`). It covers Tamil / English / Tanglish, relative dates, one question when date or time is missing, asking to repeat unclear speech, refusing unrelated topics, and treating titles and notes as data. Replies are cleaned server-side (no markdown, bullets, emojis; max 400 characters). The model ends a question with `[[ASK]]` (removed before sending); that sets `needs_more_info`.

**Tool rounds.** At most 4 rounds of tool calls per command; the request after the 4th is sent with tools switched off so the model has to answer.

**Provider formats** (checked against the official docs on 2026-09-29; see the header of `app/ai_providers.py`): Gemini `functionDeclarations` + `functionCall` / `functionResponse` (ids and thought signatures echoed back); OpenAI Responses `function` tools (strict) + `function_call` / `function_call_output` (reasoning items replayed, `store:false`).

**Errors** (English + Tamil): `invalid_key`, `quota_exceeded`, `timeout`, `network_error`, `provider_error`, `model_not_found`, `ai_not_configured`, plus the two action errors above.

**Windows note:** timezone names need the `tzdata` package (add `tzdata` to `requirements.txt` if it is not there). Without it the assistant falls back to a fixed +05:30.

Tests: `tests/test_assistant.py` (provider mocked at the HTTP layer, no network).

## Scheduler + Twilio reminder calls
A background job (APScheduler, every `SCHEDULER_INTERVAL_SECONDS`, default 60) looks at the existing deadlines and phones you **once** per reminder.

**When is a reminder due?** Exactly by the existing settings: *(deadline date minus "Call how early?") at "Call time", in your timezone*. Only for pending deadlines, only if **Proactive calls** is on and consent was given. A reminder missed by more than 6 hours (server was off) is skipped, not called late. At most 5 calls per tick.

**No duplicates.** Each call is claimed with one atomic conditional UPDATE on `call_attempts` before Twilio is contacted, so overlapping ticks or two server processes cannot both make the same call or the same retry. Completing a repeating deadline or moving its date resets the markers (existing behaviour), so the next cycle gets its own call. (4B: a failed call is now retried, see below.)

**Who is called?** `NIRA_USER_PHONE_NUMBER`, and only for the account whose *verified* phone is that same number. The destination is never taken from a request.

**Config (server env / `.env` only, never in the browser)**
| Variable | Meaning |
|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | from the Twilio Console (also used by SMS OTP) |
| `TWILIO_PHONE_NUMBER` | your Twilio number (E.164) |
| `NIRA_USER_PHONE_NUMBER` | the phone to call (E.164, e.g. `+91...`) |
| `SCHEDULER_ENABLED` | `false` switches the scheduler off |

If any value is missing or not E.164, the scheduler does not start (a warning names the missing variable, never its value) and `POST /calls/test` answers `twilio_not_configured`.

**Test call.** Settings -> *Proactive calls* card -> **Make Test Call**, or `POST /calls/test` (login required, `TEST_CALL_RATE_LIMIT` default 1/minute). One call, fixed message, to `NIRA_USER_PHONE_NUMBER`. Errors (English + Tamil): `twilio_not_configured` 503, `call_forbidden` 403, `call_failed` 502.

**Tests.** `tests/test_calls.py` uses a fake Twilio client; an autouse fixture in `tests/conftest.py` makes the real client impossible to build in any test.

## 4B: call results, retries and call history
**Attempt vs success.** `call_attempts` on the deadline counts *attempts*. `last_call_at` is set **only** when Twilio reports the call was answered (`in-progress` / `completed`); the API's `called` flag means exactly that. A failed Twilio request, or a busy / no-answer / failed call, leaves the reminder un-called and retryable. No new columns: history uses the existing `call_logs` table (one row per call, `attempt` 1 = first call, 2 = first retry, ...).

**Statuses stored** (Twilio's own words): `queued, ringing, in-progress, completed, busy, failed, no-answer, canceled`. A call only moves forward, a finished call is final, so repeated or late callbacks change nothing.

**Retry.** After `failed`, `no-answer` or `busy` (or a failed request to Twilio) the reminder gets `next_retry_at = now + CALL_RETRY_DELAY_MINUTES` (default 10). The scheduler retries it when due, at most `CALL_MAX_RETRIES` (default 2) times, so at most 3 calls per reminder. `canceled` is final. No retry once the call was answered, the reminder is done, or its due time has passed. The 6-hour missed-call rule and the 5-calls-per-tick cap still apply (a retry is not held to the 6-hour window, since it follows a call made inside it).

**Callbacks.** Twilio posts each call's status to `POST /twilio/status`. There is no login on this URL; every request must carry a valid `X-Twilio-Signature` made with `TWILIO_AUTH_TOKEN`, otherwise it gets 403 and nothing is read. The URL Twilio is given is `TWILIO_STATUS_CALLBACK_URL`, or `APP_BASE_URL` + `/twilio/status` when `APP_BASE_URL` is a public https address. On plain `localhost` no callback URL is sent; instead, a call with no result after 10 minutes is checked directly with Twilio (`GET .../Calls/{sid}`), so history and retries still work. To test callbacks locally, run a tunnel (for example ngrok) and put its `https://.../twilio/status` in `TWILIO_STATUS_CALLBACK_URL`.

**History.** `GET /calls/history` (login required, your own calls, newest first, max 200): `title, phone (masked), call_time, status, retry_count, duration_sec`. The Calls tab shows it (Make Test Call appears there too as "Test call"). Twilio ids and error text are never returned.

Tests: `tests/test_call_results.py` (fake Twilio, signed fake callbacks; no network).

## 4B setup guide (Twilio calls, callbacks, retries)

### Required environment variables (server `.env` only, never the browser)
| Variable | Meaning |
|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Twilio Console credentials. The auth token also signs the status callbacks. |
| `TWILIO_PHONE_NUMBER` | your Twilio voice number, E.164 (calls come FROM it) |
| `NIRA_USER_PHONE_NUMBER` | the one phone NIRA calls, E.164; must differ from `TWILIO_PHONE_NUMBER` |
| `APP_BASE_URL` | the public address of this server, e.g. `https://your-app.example.com` (used to build the callback URL) |
| `TWILIO_STATUS_CALLBACK_URL` | optional full callback URL; wins over `APP_BASE_URL` |
| `CALL_MAX_RETRIES` | retries after the first call (default 2, so at most 3 calls per reminder) |
| `CALL_RETRY_DELAY_MINUTES` | wait before each retry (default 10) |
| `SCHEDULER_ENABLED`, `SCHEDULER_INTERVAL_SECONDS` | scheduler switch and tick length |
| `TEST_CALL_RATE_LIMIT` | limit for Make Test Call (default `1/minute`) |

If a Twilio value is missing or not E.164, the scheduler stays off and `POST /calls/test` answers `twilio_not_configured`.

### Configure the Twilio callback
1. Make NIRA reachable at a public **https** address (a deployed server, or a tunnel while developing).
2. Set `APP_BASE_URL` to that address. NIRA then tells Twilio, **per call**, to post results to `APP_BASE_URL/twilio/status`. If your callback address differs from `APP_BASE_URL`, set `TWILIO_STATUS_CALLBACK_URL` to the full address instead.
3. Nothing needs to be configured in the Twilio Console: the callback URL travels with each call request. Do not put a different URL on the number in the Console for this purpose.
4. The URL must match exactly what Twilio calls: NIRA verifies `X-Twilio-Signature` against that URL and `TWILIO_AUTH_TOKEN`. Requests with a missing or wrong signature get 403 and nothing is read. Bodies over 20 KB get 413.
5. Behind a proxy or a changed hostname, set `TWILIO_STATUS_CALLBACK_URL` explicitly, so the signed URL is exactly the one Twilio uses.

### Local development
* On `http://localhost` no callback URL is sent (Twilio cannot reach your PC). Calls still work: a call with no result after 10 minutes is checked directly with Twilio, and history/retries follow from that answer. Results arrive up to about 10 minutes late in this mode.
* To test real callbacks, start a tunnel (for example `ngrok http 8000`) and put `https://<tunnel-address>/twilio/status` in `TWILIO_STATUS_CALLBACK_URL`. Restart NIRA after changing `.env`.
* Twilio trial accounts can only call verified numbers and play a trial message first.

### Retry behaviour
* `failed`, `no-answer`, `busy` (or a request Twilio refused): retried after `CALL_RETRY_DELAY_MINUTES`, at most `CALL_MAX_RETRIES` times.
* `in-progress` / `completed` = answered: the reminder counts as called, never retried. `canceled` is final.
* No retry when the reminder is done, past its due time, or calls / consent were switched off.
* A retry claims its slot with an atomic update, so it can never run twice. Duplicate, late or out-of-order callbacks are ignored.

### Single-number limitation
NIRA calls exactly one phone: `NIRA_USER_PHONE_NUMBER`, and only for the account whose **verified** phone is that same number. Other accounts get no calls and cannot use Make Test Call. Per-user numbers are a later phase.

### Run the app
```bash
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env           # Mac/Linux: cp .env.example .env   (then edit the values)
python run.py                    # open http://localhost:8000
```

### Run the tests
```bash
python -m pytest -q
```
Every test uses a fake Twilio client and an in-file SQLite database (`test_nira.db`); an autouse fixture makes the real Twilio client impossible to build, so **no test can place a real call or reach the network**. `tests/conftest.py` pins the settings the tests rely on, so your own `.env` does not change results.

### Known limitations
* Reminder calls come from an in-process scheduler (APScheduler). It runs only while the server process runs: free hosts that sleep or restart (for example Render's free plan) will miss reminders. A reminder missed by more than 6 hours is skipped. Use an always-on host and keep `SCHEDULER_ENABLED=true` on **one** instance (duplicate calls are still prevented by the atomic claim, but extra instances waste work).
* Tables are created with `create_all`; no migrations yet.
* If the server stops after Twilio accepts a call but before NIRA saves the call id, that call is marked failed after 10 minutes and may be retried, so in that rare crash window one extra call is possible.
* Calls with no result after 24 hours stay as they are (no automatic retry).
* Call text is fixed English; Tamil call scripts and keypad actions during the call are later phases.

## 5A: Deploy on Render (PostgreSQL + Twilio)
One Render web service (API + web app + reminder scheduler) and one Render PostgreSQL database, created from `render.yaml`. The app is served from the same address as the API, so the frontend needs no API address setting and there is no CORS setup beyond `CORS_ORIGINS`.

### Before you start (5 minutes)
1. Put the project in a GitHub repository (the `.env` file and `*.db` files are git-ignored: do not commit them).
2. Create a Twilio account. Note the **Account SID** and **Auth Token** (Console home), and buy or use a Twilio **voice-capable phone number**.
3. Generate two keys on your PC:
   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # -> ENCRYPTION_KEY
   ```
   (`SECRET_KEY` is generated for you by Render.) Save `ENCRYPTION_KEY` somewhere safe: if you lose or change it, users' saved AI keys can no longer be read (they just save them again).

### Deploy
1. Render Dashboard -> **New** -> **Blueprint** -> select your repository. Render reads `render.yaml` and proposes the web service `nira` (plan `starter`) and the database `nira-db` (plan `basic-256mb`). `DATABASE_URL` is wired automatically; both use the same region.
2. Fill in the values Render asks for (see the table below). You do not know your public address yet: type `https://nira.onrender.com` as a placeholder for `APP_BASE_URL` and `CORS_ORIGINS`, and fix it in step 4. (If that name is taken, Render adds a suffix; use whatever address it shows.)
3. Click **Apply**. The first build takes a few minutes. The service is healthy when `https://<your-address>/health` shows `{"ok": true}`.
4. Copy the real address from the top of the service page (`https://<name>.onrender.com`). Open **Environment**, set `APP_BASE_URL` and `CORS_ORIGINS` to exactly that address (https, no trailing slash), and save: Render redeploys.
5. Open the address, sign up with your phone number (the OTP arrives as an SMS), verify the phone.
6. In **Settings** switch on *Proactive calls*, then tap **Make Test Call**. Your phone rings; the **Calls** tab shows the call and its result.

If the service refuses to start, open **Logs**: the message starts with `Unsafe production config` and names each wrong setting (never its value).

### Environment variables
| Variable | Value |
|---|---|
| `APP_ENV` | `prod` (set in `render.yaml`) |
| `DATABASE_URL` | filled automatically from the Render database |
| `SECRET_KEY` | generated by Render (32+ random characters) |
| `ENCRYPTION_KEY` | the Fernet key you generated above |
| `APP_BASE_URL` | `https://<your-service>.onrender.com`. Must be https: it builds magic-link emails **and** the Twilio callback address |
| `CORS_ORIGINS` | the same address as `APP_BASE_URL` |
| `SMS_PROVIDER` | `twilio` (set in `render.yaml`; prod refuses `console`) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | from the Twilio Console |
| `TWILIO_FROM` | Twilio number that sends the OTP SMS (E.164) |
| `TWILIO_PHONE_NUMBER` | Twilio number that makes the reminder calls (E.164, may be the same number) |
| `NIRA_USER_PHONE_NUMBER` | the one phone NIRA calls (E.164, e.g. `+91...`); must differ from `TWILIO_PHONE_NUMBER` |
| `SCHEDULER_ENABLED` | `true` (set in `render.yaml`) |

Optional, add in the Render dashboard if you want them: `DEFAULT_AI_PROVIDER` (`gemini`/`openai`), `DEFAULT_AI_API_KEY`, `DEFAULT_AI_MODEL` (a server-wide AI fallback for the voice assistant; users can also save their own key in Settings -> AI); `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (needed only for email magic-link sign-in; without them, magic links fail in prod and phone or email+password sign-in still work); `CALL_MAX_RETRIES`, `CALL_RETRY_DELAY_MINUTES`; `TWILIO_STATUS_CALLBACK_URL` (only if the callback address differs from `APP_BASE_URL`).

### Twilio setup for production
1. **Voice permissions:** Console -> Voice -> Settings -> **Geo permissions**: enable the country of `NIRA_USER_PHONE_NUMBER` (for example India). Otherwise Twilio refuses the call and the history shows `failed`.
2. **Status callback:** nothing to set in the Twilio Console. NIRA sends the callback address (`APP_BASE_URL` + `/twilio/status`) with every call, and checks the `X-Twilio-Signature` of every callback with `TWILIO_AUTH_TOKEN`. Because the signature covers the address, `APP_BASE_URL` must be exactly the public https address (no trailing slash, no different hostname).
3. **Trial accounts** can only call and text numbers you have verified in the Console, and play a short trial message before the call.
4. **SMS to India:** sending SMS to Indian numbers usually needs sender/template registration (DLT) with Twilio. If OTP SMS is refused, this is the usual reason; it is a Twilio/regulatory matter, not an app setting.
5. Keep `TWILIO_AUTH_TOKEN` only in Render's environment. Never put it in the repository or the browser.

### After the first deploy: quick checks
* `https://<address>/health` -> `{"ok": true}`; `/docs` is intentionally off in production.
* Sign in, add a deadline, ask the Voice assistant something (needs an AI key: yours in Settings -> AI, or `DEFAULT_AI_API_KEY`).
* Make Test Call -> the phone rings -> the Calls tab moves from `Queued` to `Completed` (this shows the callback works). If it stays `Queued`, check `APP_BASE_URL`: results are then only picked up after about 10 minutes.
* Install as an app: open the address on the phone and use "Add to Home screen" (needs https, which Render provides).

### Production limitations
* **Use a paid, always-on web plan.** The scheduler runs inside the web process. On a plan that sleeps (the free plan) nobody wakes the app at reminder time, so calls are missed. Run **one** instance only (do not scale out and do not add `--workers`); the atomic claim still prevents duplicate calls if two instances briefly overlap during a deploy.
* A reminder missed by more than 6 hours (for example during downtime) is skipped, not called late.
* The database is a paid `basic-256mb` plan in `render.yaml`. The free database is time-limited and would lose your data; switch both plans to `free` only for a throw-away test.
* Tables are created on start-up (`create_all`); there are no migrations yet. Adding a column to an existing table later needs a manual change or migrations (planned for later).
* One phone number only (`NIRA_USER_PHONE_NUMBER`), as described above.
* No automated backups beyond what your Render database plan provides.
* `render.yaml` and the deployment steps here were checked by reading and by parsing the YAML; a real Render deploy was **not** performed from this environment.

## Running without Twilio (SMS_PROVIDER=off)
`render.yaml` is set up so the app runs on Render with no Twilio account: `SMS_PROVIDER=off`, `SCHEDULER_ENABLED=false`, free plans.
Sign up with **email + password**, then tap **Skip for now** on the phone screen. Deadlines and the AI assistant work; phone OTP login, reminder calls and the test call do not.
To add calls later: set `SMS_PROVIDER=twilio`, the Twilio variables, `SCHEDULER_ENABLED=true`, and a paid always-on plan.
