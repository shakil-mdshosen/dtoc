# Google Sheets Sync — Design Plan

**Status:** Draft for review — design only, no implementation in this PR
**Date:** 2026-11-23
**Repo:** `shakil-mdshosen/dtoc` (`master` at `3a7c215`, 2026-09-26)
**Live tool:** https://dtoc.toolforge.org
**Author:** Shakil Hosen (MdsShakil), with Muse
**Reviewers requested:** GitHub Copilot (automated review), Shakil Hosen (owner decision, see Open Questions)

## 1. Summary

Add an **optional, per-form** Google Sheets sync to DTOC, modelled on Google Forms:

1. A form creator turns on "Sync to Google Sheets" for one form.
2. DTOC asks that creator to connect their Google account and grant permission to edit the target spreadsheet.
3. DTOC creates a new spreadsheet (v1) named after the form, writes one header row, backfills existing responses, and then appends one row per new submission — live, in the same column order as the existing CSV / XLSX export.
4. Sync can be paused, retried, and disconnected at any time. Disconnecting stops future syncs; it does not delete the spreadsheet or any rows already written.

This is a design document. It does not add the Google client, credentials, or any live Google call. It is the contract the implementation PR must follow, so that review can check the code against it.

### Goals

- Google-Forms-like experience for form creators who already live in Sheets.
- One spreadsheet per form, one row per submission, headers identical to export.
- Sync failure must never break form submission, and must never lose a response.
- Least-privilege Google access, encrypted token storage, and a clear privacy notice before any response data leaves Toolforge.
- Reuse existing DTOC patterns wherever possible: Wikimedia OAuth flow, `sanitize_settings`, `column_headers` / `format_value`, `ensure_schema_upgrades`, `AuditLog`, and the `toolforge-jobs` retry pattern used by `janitor.py`.

### Non-goals (v1)

- Picking an existing spreadsheet, or syncing one form to several sheets. (Phase 4.)
- Two-way sync: edits made directly in Sheets are never written back to DTOC.
- Syncing form edits, response edits, or response deletions to Sheets. See §8.
- Sharing a sheet with other collaborators from inside DTOC; sharing stays in Google Drive.
- A service-account mode. See §13, Alternatives.
- Real-time collaboration, charts in Sheets, or a Google Workspace add-on.

## 2. Current state (verified 2026-11-23)

| Area | What exists today | File / route |
|---|---|---|
| App | Flask factory, 3 blueprints (`auth`, `forms`, `export`), `db.create_all()` + `ensure_schema_upgrades()` on startup | `app.py:5-41` |
| Auth | Wikimedia OAuth 2.0 only. `safe_next_url` blocks open redirects, `state` in session, `redirect_uri` forced to HTTPS, no tokens stored in session after login | `auth.py:13-124` |
| Config | `WIKI_CLIENT_ID` / `WIKI_CLIENT_SECRET` from env; MariaDB on Toolforge via `~/replica.my.cnf`, SQLite locally; `pool_pre_ping`, `pool_recycle=300` | `config.py` |
| Models | `Form` (JSON `schema`), `Submission` (JSON `data`, `submitted_by`, `submitted_email`), `Permission` (`admin`/`editor`/`viewer`), `AuditLog`. New nullable columns are added idempotently in `ensure_schema_upgrades()` | `models.py:1-70` |
| Submission | `POST /form/<id>` validates with `validate_submission()`, stores `data` keyed by field `key`, commits, returns 201. No external call after commit today | `forms_api.py:373-416` |
| Schema | `sanitize_settings()` allow-lists settings (`theme_color`, `one_response_per_user`, `response_limit`, `close_at`, `collect_email`, …). `column_headers()` = input fields in order + legacy keys. `format_value()` joins lists with `, ` and dicts as `k: v` | `form_schema.py:175-194, 416-435` |
| Export | CSV / JSON / XLSX share `_load_export()` (permission check + `AuditLog`), meta columns `ID, Submitted At, Submitted By[, Submitted Email]` + question headers | `export.py:14-148` |
| Permissions | `can_manage()` = owner or `admin` permission, `can_edit()` = owner / `admin` / `editor`. Only the form owner can close / delete / manage collaborators | `forms_api.py:178-199, 508-584` |
| Retention | `janitor.py` purges submissions of forms closed >90 days, keeps the `Form` and `AuditLog` rows. Daily `toolforge-jobs` job in `setup_jobs.sh` | `janitor.py`, `setup_jobs.sh` |
| Privacy | `templates/privacy.html` states: no third-party scripts/fonts/images, no analytics or tracking, data not sold or shared except as required by law / Cloud Services Terms | `templates/privacy.html:71-84, 110` |
| Tests | `tests/test_*.py` (pytest). `test_oauth.py` is a manual script, not a pytest test | `tests/`, `test_oauth.py` |
| Google | **Nothing.** No Google dependency in `requirements.txt`, no Google code, no Google credentials anywhere in the repo | `requirements.txt` |

Consequences for the design: the Sheets mapping, the export columns, and the OAuth redirect/state pattern already exist and must be reused, not reinvented. The privacy page, the settings allow-list, and the schema-upgrade helper are the three places most likely to be forgotten in implementation, so they are explicit checklist items in §12.

## 3. User experience

### 3.1 Happy path (form creator, who is the form owner in v1)

1. Owner opens a form in the builder, Settings tab (where `collect_email` already lives, `templates/builder.html`, `static/js/builder.js`).
2. Owner sees a "Google Sheets" section: status `Not connected`, a short explanation that responses will be copied to Google, and a **Connect Google Sheets** button. The section is hidden entirely when Google sync is not configured on the instance (see §4.5).
3. Clicking it starts Google OAuth ( §5 ). The creator picks a Google account and consents to the two scopes in §5.2.
4. Back on DTOC, the creator chooses **Create new spreadsheet**. DTOC creates it in that Google account's Drive, names it after the form title, writes the header row, and shows a link "Open spreadsheet" plus a status `Synced — N rows`.
5. Every new submission appends a row within seconds. The submissions page (`/form/<id>/submissions`) shows the sync status and the last sync time / last error.
6. The creator can, at any time: **Sync now** (backfill any missed rows), **Pause sync**, **Resume sync**, or **Disconnect**. Disconnect stops future writes and deletes DTOC's stored Google token for that form ( §5.5 ); the spreadsheet itself stays in the creator's Drive.

### 3.2 Respondent experience

- No change, and no Google prompt. Respondents keep logging in with Wikimedia only. They must be told, on the form page, that responses are also copied to a Google Sheet owned by the form creator, before they submit. The exact wording is a privacy-page / form-notice update, not a new consent checkbox in v1 — see §10.
- If Google is down, slow, or the token is revoked, the respondent still gets a normal "Submission received" (201). Sync is retried in the background (§7).

### 3.3 Failure UX

| State | What the owner sees | What happens to responses |
|---|---|---|
| Never connected | `Not connected` | Stored in DTOC only |
| Connected, healthy | `Synced — N rows · last sync HH:MM` | Appended live |
| Pending retries | `Sync delayed — retrying (M rows pending)` | Queued, retried (§7.3) |
| Token revoked / sheet deleted / access removed | `Sync paused — reconnect Google` + reconnect button | Queued for 7 days, then marked failed and kept in DTOC; owner is told the queue expired |
| Disconnected by owner | `Not connected` | Stored in DTOC only; no further writes |

## 4. Architecture

```
Respondent (Wikimedia login)
  -> POST /form/<id>  (forms_api.view_form)
  -> validate_submission() -> Submission row in MariaDB -> 201
  -> enqueue SyncQueue row (same DB transaction)      [never blocks the 201]
  -> [background] sync worker
       -> load GoogleAccount refresh token (decrypt)
       -> Sheets API values.append / values.batchUpdate
       -> mark queue row sent / failed, update SheetSync.last_synced_at
       -> on 401, refresh token; on 403/404, pause sync + surface error

Form owner (Wikimedia login + Google login)
  -> Builder Settings -> /google/connect -> Google OAuth consent
  -> /google/callback -> store encrypted refresh token (GoogleAccount)
  -> /form/<id>/sheets/create -> spreadsheets.create + header row + backfill
  -> Dashboard / submissions page shows SheetSync status
```

### 4.1 New modules (all new files, no changes to the Wikimedia login)

| File | Responsibility |
|---|---|
| `google_sheets.py` | Pure Sheets client wrapper: create spreadsheet, ensure header row, append rows, read sheet metadata. No Flask request state; takes a `Credentials` object. All Google API errors are translated to `SheetsError` kinds (`auth`, `not_found`, `forbidden`, `quota`, `transient`). |
| `google_auth.py` | Flask blueprint `google_bp`: `/google/connect`, `/google/callback`, `/google/disconnect`, plus the per-form routes in §6. Reuses `safe_next_url`, session `state`, and the HTTPS `redirect_uri` pattern from `auth.py`. |
| `sync_worker.py` | Queue consumer: claim pending rows, call `google_sheets.py`, update status, exponential backoff. Runnable as `python sync_worker.py` from a `toolforge-jobs` schedule, like `janitor.py`. Must be safe to run concurrently / twice — claim rows with a single atomic update. |
| `google_tokens.py` (or inside `google_auth.py`) | Token encryption only: `encrypt_refresh_token` / `decrypt_refresh_token` with Fernet. No other module touches raw tokens. |

### 4.2 Dependencies

Add to `requirements.txt`, pinned like the existing entries:

```
google-api-python-client==2.160.0   # Sheets + Drive v3/v4 clients (verify latest at implementation time)
google-auth-oauthlib==1.2.1
google-auth==2.35.0
cryptography==43.0.0               # Fernet for refresh-token encryption
```

No new frontend dependency, no CDN, no Google Fonts — the privacy invariant in `templates/privacy.html:71` stays true for the browser. Google is contacted server-side only.

### 4.3 Configuration (Toolforge envvars, never committed)

| Variable | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` | Google Cloud OAuth client ID (Web application) |
| `GOOGLE_CLIENT_SECRET` | Google Cloud OAuth client secret |
| `GOOGLE_TOKEN_KEY` | Fernet key (44-char base64) used only to encrypt refresh tokens. Distinct from Flask `SECRET_KEY`. Rotate by re-encrypting stored tokens; a missing key must fail startup loudly, not fall back to a dev default. |
| `GOOGLE_SYNC_ENABLED` | `1` / `0`. When `0` or unset, the Sheets UI is hidden and all Google routes return 404 / disabled. This is the kill-switch and the way to run the tool with no Google configured. |

Add all four to `config.py` as `os.environ.get(...)` with no secret defaults, following the existing `WIKI_CLIENT_ID` pattern. Document them in `README.md` next to the existing env-var table.

### 4.4 What is *not* introduced

- No service account, no shared Google account for all forms, no storing of a Google password.
- No Google data in the Flask session (Wikimedia `auth.py` already avoids this for its own tokens; do the same here — only the Wikimedia username stays in session, Google tokens stay in the DB, encrypted).
- No new database server: the queue and the sync tables live in the same MariaDB / SQLite database as `Form` / `Submission`, so a sync row and its submission row commit or roll back together.

### 4.5 Feature flag / rollout gate

`GOOGLE_SYNC_ENABLED=0` (default) means the feature does not exist from the user's point of view. Phase 1 ( §12 ) ships with the flag off on Toolforge, on only in local development, until the Google verification in §11 is at least in "Testing" mode with Shakil as a test user.

## 5. OAuth 2.0 — the part that must be right

This is a **second, independent OAuth flow**. The Wikimedia login proves who the creator is on Wikimedia; the Google login proves which Google account owns the spreadsheet and grants edit access to it. The two identities are linked only by the Wikimedia username in DTOC's database — never assume the Google email equals the Wikimedia username or email.

### 5.1 Google Cloud setup (manual, once, by the tool operator — not code)

1. Create (or reuse) a Google Cloud project. Record who owns it — see Open Question 1 in §14.
2. Enable **Google Sheets API** and **Google Drive API**.
3. Create OAuth consent screen: app name "DTOC", homepage `https://dtoc.toolforge.org`, privacy policy URL `https://dtoc.toolforge.org/privacy`, authorised domain `toolforge.org`.
4. Create OAuth client ID, type **Web application**:
   - Authorised redirect URI (production): `https://dtoc.toolforge.org/google/callback`
   - Authorised redirect URI (local dev): `http://127.0.0.1:5000/google/callback` — only for local testing, never for production.
5. Put the client ID / secret in Toolforge envvars (`toolforge envvars create GOOGLE_CLIENT_ID …`), never in git, never in a log.
6. Until verification (§11), add test users in Google Cloud Console. Only those accounts can complete the flow.

### 5.2 Scopes — least privilege

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/spreadsheets` | Create the spreadsheet, write headers and rows, read sheet metadata. Required. |
| `https://www.googleapis.com/auth/drive.file` | Create files in Drive and open files the user picks / the app created. Required for v1 (create). **Do not** request full `https://www.googleapis.com/auth/drive` in v1 — it sees every file in the account and will make verification harder and the consent screen scarier. |

Request `access_type=offline` and `prompt=consent` on the first connect, so Google returns a refresh token. Request `include_granted_scopes=true` only if incremental scopes are added later; do not silently widen scopes.

The consent screen must show, in plain language, that DTOC will create / edit **one spreadsheet per form the creator connects**, and copy that form's responses into it. That wording must match the privacy page (§10).

### 5.3 Flow (mirror `auth.py`, do not copy its debug logging)

```
GET /google/connect?next=/form/42/edit
  - require Wikimedia login (login_required), require can_manage(form) — owner only in v1, §6
  - generate random state, store in session['google_oauth_state'] + the return URL in session['google_return_to'] (validated with safe_next_url)
  - redirect to accounts.google.com/o/oauth2/v2/auth
      ?client_id=…&redirect_uri=https://dtoc.toolforge.org/google/callback
      &response_type=code&scope=<two scopes above, space-separated>
      &access_type=offline&prompt=consent&state=<state>

GET /google/callback?code=…&state=…
  - if state missing / mismatched, or error=access_denied: flash, redirect to return URL. No token stored.
  - exchange code for tokens (server-to-server POST to oauth2.googleapis.com/token, 10s timeout, User-Agent dtoc-toolforge/1.0)
  - on failure: flash a generic message, log only the HTTP status (never the code, token, or full error body)
  - fetch the Google profile (userinfo) to get the Google email for display only
  - encrypt the refresh token with GOOGLE_TOKEN_KEY, upsert GoogleAccount(wikimedia_username, google_email, encrypted_refresh_token, scopes, updated_at)
  - clear session state, redirect to return URL
```

Security requirements, all testable:

- `state` must be a fresh cryptographically random value per attempt, checked with a constant-time compare, single-use (popped from session).
- `redirect_uri` must be exactly the registered URI, built with `url_for(..., _external=True)` and forced to HTTPS in production, as `auth.py:51-55` does.
- The `code` is exchanged once, server-side, and never logged, never put in a URL, never stored.
- The access token is held in memory only for the request that needs it; the refresh token is the only long-lived secret, and it is encrypted at rest (§5.4).
- **Do not repeat `auth.py`'s `print(f"DEBUG: …")` logging.** Token exchange responses, profile payloads, and Google error bodies must not be printed or logged. Log at most: event name, form ID, Google HTTP status, and a short error kind.
- CSRF: the connect / disconnect / create-sheet actions are POSTs (or GET only for the OAuth start, which carries `state`). Disconnect and pause must not be GET links that another site can trigger.

### 5.4 Token storage and encryption

- Table `GoogleAccount` (§6.1). Columns: `wikimedia_username` (unique, the link key), `google_email` (display only), `encrypted_refresh_token` (TEXT, Fernet ciphertext), `scopes` (TEXT, space-separated, as granted), `created_at`, `updated_at`.
- Encryption: `cryptography.fernet.Fernet(GOOGLE_TOKEN_KEY)`. The key is generated once with `Fernet.generate_key()` and stored only in Toolforge envvars. If the key is missing in production, the Google blueprint must refuse to start the flow with a clear error, not generate a throwaway key and silently make stored tokens undecryptable after a restart.
- Decryption failure (wrong / rotated key) is treated as "token unusable": pause sync, ask the owner to reconnect, log the event kind only. Never log the ciphertext or the key.
- Refresh: on a 401 from the Sheets API, refresh the access token with the stored refresh token, retry once, and if the refresh fails with `invalid_grant`, mark the account disconnected and pause all its syncs. That is the revoked-token path.
- Revocation on disconnect: call Google's revoke endpoint for the refresh token, then delete the `GoogleAccount` row and mark its `SheetSync` rows `disconnected`. Deleting the row without revoking leaves a live token in Google's hands; revoking without deleting leaves a dead row that can confuse status checks. Do both, in that order, and log an `AuditLog` entry.
- One Google account per Wikimedia user in v1. If the owner connects a different Google account later, that replaces the stored token (after an explicit "switch account" confirmation), and existing syncs keep pointing at the old spreadsheets until the owner reconnects / re-creates them. Do not silently re-point old sheets at a new account.

### 5.5 Who can connect what

| Action | Who (v1) |
|---|---|
| Connect / switch / disconnect *their own* Google account | Any logged-in Wikimedia user, for their own account only. The stored row is keyed by Wikimedia username. |
| Enable sync on a form, create its spreadsheet, pause / resume / disconnect *that form's* sync | Form owner only (`created_by`, or `is_owner_user`). Editors and viewers can see the sync status, but cannot change it. Rationale: the spreadsheet lives in the owner's Google Drive and contains respondent data; only the owner should authorise that export. Revisit in Phase 4 if editors need it, with an explicit owner opt-in. |
| View sync status | Owner, editors, viewers (read-only badge on dashboard / submissions page). |

## 6. Data model and API

### 6.1 New tables

All new tables are created by `db.create_all()` for a fresh install, and any column added later follows the existing `ensure_schema_upgrades()` idempotent-`ALTER` pattern in `models.py`. No migration framework is introduced in v1.

```python
class GoogleAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    wikimedia_username = db.Column(db.String(255), nullable=False, unique=True)
    google_email = db.Column(db.String(255), nullable=True)  # display only
    encrypted_refresh_token = db.Column(db.Text, nullable=False)
    scopes = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SheetSync(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False, unique=True)
    # one spreadsheet per form in v1
    spreadsheet_id = db.Column(db.String(255), nullable=False)
    spreadsheet_url = db.Column(db.String(512), nullable=True)
    sheet_title = db.Column(db.String(255), nullable=False, default='Responses')
    # 'active' | 'paused' | 'error' | 'disconnected'
    status = db.Column(db.String(50), nullable=False, default='active')
    last_error = db.Column(db.Text, nullable=True)  # short kind + message, no tokens
    header_version = db.Column(db.Integer, nullable=False, default=1)
    # JSON list of field keys in column order, frozen at header creation
    columns_json = db.Column(db.Text, nullable=False, default='[]')
    last_synced_at = db.Column(db.DateTime, nullable=True)
    last_synced_submission_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.String(255), nullable=False)  # Wikimedia username
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SyncQueue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)
    submission_id = db.Column(db.Integer, db.ForeignKey('submission.id'), nullable=False)
    # 'pending' | 'sent' | 'failed' | 'skipped'
    status = db.Column(db.String(50), nullable=False, default='pending')
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    next_retry_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # unique pending row per submission, so a double-submit cannot queue twice:
    # enforced in code + a unique index on (submission_id) where status='pending'
    # (MariaDB partial indexes are limited; enforce in code and de-duplicate in the worker)
```

Notes:

- `SheetSync.columns_json` is the frozen header mapping. It is the single source of truth for row order. It is built once from `column_headers(schema, [])` plus the meta columns in §8, and only appended to (never reordered) when the form gains a new question. See §8.3.
- `SyncQueue` rows are created in the same transaction as the `Submission` row (§7.1). A submission with no queue row is a bug; the "Sync now" backfill must be able to rebuild queue rows from `Submission` rows whose ID is greater than `last_synced_submission_id`.
- `AuditLog` already exists: log `GOOGLE_CONNECT`, `GOOGLE_DISCONNECT`, `SHEET_CREATE`, `SHEET_SYNC_PAUSED`, `SHEET_SYNC_RESUMED`, `SHEET_EXPORT_FAILED` (with form ID and counts, never with answer data or tokens).

### 6.2 New routes

All under the new `google_bp` blueprint, registered in `app.py` next to `auth_bp` / `forms_bp` / `export_bp`. All require Wikimedia login; per-form routes require the owner check in §5.5.

| Route | Method | Purpose |
|---|---|---|
| `/google/connect?next=…` | GET | Start OAuth (§5.3) |
| `/google/callback` | GET | OAuth callback (§5.3) |
| `/google/disconnect` | POST | Revoke + delete own `GoogleAccount` (§5.4) |
| `/form/<id>/sheets/connect` | POST | Create spreadsheet + header row + backfill queue ( §7.2 ). Owner only. Returns the spreadsheet URL. |
| `/form/<id>/sheets/disconnect` | POST | Pause sync for this form, mark `SheetSync` disconnected. Does not delete the spreadsheet. Owner only. |
| `/form/<id>/sheets/sync-now` | POST | Enqueue all unsynced submissions for this form, trigger the worker path once. Owner / editor. Rate-limited: at most once per minute per form. |
| `/form/<id>/sheets/status` | GET (JSON) | `{status, spreadsheet_url, last_synced_at, pending_count, last_error}` for the dashboard badge. Same permission as viewing submissions. |

Do not add a route that lists all of a user's Google Drive files, and do not add a route that takes an arbitrary spreadsheet ID from the user in v1 — that is the Phase 4 "existing sheet" work, and it needs the Drive picker / permission check designed separately.

### 6.3 Settings storage

Do **not** add a `google_sheets` key to the form `schema.settings` JSON in v1. The sync state lives in `SheetSync`, not in the schema, because:

- The schema is edited by editors as well as owners, and is copied on form duplication (`forms_api.py:548-566`). A duplicated form must not silently inherit a live sync to the original owner's spreadsheet.
- `sanitize_settings()` is an allow-list; adding a settings key there would make the builder able to turn sync on without the OAuth step. The builder button must call the POST route in §6.2, not set a settings flag.

If a later phase wants a settings toggle, it must be a display-only reflection of `SheetSync.status`, not the source of truth.

## 7. Sync engine

### 7.1 Live path — enqueue, never block

In `forms_api.view_form`, after `db.session.commit()` for the new `Submission` (currently `forms_api.py:411-413`):

```python
# after commit, only if a SheetSync row exists for this form and is active:
#   create SyncQueue(form_id=…, submission_id=submission.id, status='pending')
#   in its own short transaction; if that insert fails, log it — the
#   submission itself has already succeeded and must stay succeeded.
# Then return the 201 immediately. Do NOT call Google in the request.
```

Rationale: a Google outage, a slow Sheets API, or a revoked token must not turn a successful submission into a 500, and must not make the respondent wait. The 201 means "DTOC has your response". Sync is a best-effort copy after that, with a visible status and a retry queue — exactly like the existing export, which is also a copy, not the primary store.

### 7.2 Create + backfill

`POST /form/<id>/sheets/connect` (owner only):

1. Load the form and its `GoogleAccount` for `session['username']`. If none, redirect to `/google/connect?next=…`.
2. If a `SheetSync` row already exists and is active, return its URL (idempotent — double-clicking Connect must not create a second spreadsheet).
3. `spreadsheets.create` with `properties.title = form.title` (truncated to Sheets' title limit, sanitised the same way form titles are sanitised today). Default sheet/tab title: `Responses`.
4. Build the header row (§8.1), write it with `values.update` to `Responses!A1`, store it in `SheetSync.columns_json`.
5. Create the `SheetSync` row (`status='active'`).
6. Backfill: for every existing `Submission` for the form, in `submitted_at` / ID order, create a `SyncQueue` row. Do this in batches (e.g. 500) so a form with thousands of responses does not time out the request. The worker (§7.3) then appends them in order.
7. Log `SHEET_CREATE` in `AuditLog`, flash the spreadsheet link to the owner.

### 7.3 Worker — `sync_worker.py`

Scheduled with `toolforge-jobs`, like `janitor.py` in `setup_jobs.sh`. Suggested schedule: every 5 minutes. The worker:

1. Claims up to N pending rows (e.g. 100 per run), oldest first, grouped by form, with an atomic `UPDATE … SET status='sending', attempts=attempts+1 WHERE status='pending' AND next_retry_at <= now` so two workers cannot claim the same row.
2. For each form, loads the owner's decrypted refresh token, builds a Sheets client, refreshes the access token if needed.
3. Appends rows in batches with `values.append` (`valueInputOption='USER_ENTERED'`, `insertDataOption='INSERT_ROWS'`). One API call per batch (e.g. up to 100 rows), not one call per submission — quota (§9).
4. On success: mark rows `sent`, update `SheetSync.last_synced_at` / `last_synced_submission_id`.
5. On failure, classify it ( §9 ):
   - `transient` (5xx, timeout, 429): keep `pending`, set `next_retry_at` with exponential backoff (1, 5, 15, 60 minutes), cap attempts at e.g. 20 over 7 days.
   - `auth` (401 after one refresh, `invalid_grant`): mark the `GoogleAccount` disconnected, pause the `SheetSync` (`status='error'`), stop retrying that form until the owner reconnects.
   - `not_found` / `forbidden` (404 / 403, sheet deleted or sharing removed): pause the sync, keep the queue for 7 days, surface "spreadsheet not found / access removed — reconnect or disconnect".
   - After the retry window expires: mark rows `failed`, keep them in DTOC, and show the owner a count. The data is not lost; it is in `Submission` and in the CSV export, and "Sync now" can re-queue it.
6. Logs only counts, form IDs, and error kinds. Never logs row contents, tokens, or the spreadsheet's full error body if it contains data.

The worker must be idempotent: if it crashes after appending but before marking rows `sent`, the next run may append duplicates. Mitigate by (a) recording `last_synced_submission_id` and skipping rows already at or below it on backfill, and (b) documenting in the UI that a crash during a batch can duplicate at most that batch, and that "Sync now" de-duplicates by submission ID before re-queueing. A fully exactly-once Sheets append is not possible with the Sheets API alone; do not claim it is.

### 7.4 Manual "Sync now"

Rebuilds the queue from `Submission` rows with ID greater than `last_synced_submission_id` (or all rows if the sheet was created empty), in ID order, skipping rows already marked `sent` for that form. Then runs one worker pass inline, but bounded (e.g. max 200 rows inline, the rest left for the scheduled worker), so the request does not time out.

## 8. Sheet format — must match export

### 8.1 Header row

```
ID | Submitted At | Submitted By | Submitted Email? | <question 1 key> | <question 2 key> | …
```

- Meta columns are exactly `_meta_headers()` from `export.py:34-36`: `ID`, `Submitted At`, `Submitted By`, plus `Submitted Email` only when the form's schema has `collect_email` or any existing submission has an email (`includes_email()`, `export.py:29-32`).
- Question columns are `column_headers(schema, submissions_data)` from `form_schema.py:426-435`: input fields in form order, by field `key`, plus legacy keys from older submissions. This is the same list the CSV/XLSX export uses, so a user who checks the Sheet against an export sees the same columns in the same order.
- Header values are the field `key`, not the display label. Rationale: labels change; keys are stable (see §8.3). Document this in the builder help text, because it will surprise creators who expect the question text as the header.

### 8.2 Row values

For a submission, the row is `_meta_row(sub, with_email)` + `[format_value(data.get(h, '')) for h in headers]`, exactly as `export_csv` builds it (`export.py:66-70`). Reuse those two functions — do not write a second formatter for Sheets. Consequences, which the design accepts:

- Checkbox / multi-select: comma-joined string.
- Matrix / Likert: `row_label: value; …` string from `format_value` for dicts.
- Ranking: comma-joined ordered list.
- Dates / times / numbers: sent as strings with `USER_ENTERED`, so Sheets parses dates and numbers the way it parses a pasted CSV. Document that numbers are not forced to a Sheets number format in v1.
- Empty answers: empty string, not a placeholder. Hidden-by-logic questions (not in `validate_submission` output) are empty for that row.

### 8.3 Schema changes after sync starts

- **New question added:** append a new column at the end of `columns_json` and write the new header cell. Do not insert it in the middle — that would shift every existing row's meaning. Old rows stay blank in the new column.
- **Question label changed:** header does not change (it is the `key`). No action needed.
- **Question deleted:** column stays, with its old header, and new rows leave it blank. Deleting the column would lose the history for that question.
- **Question type changed:** header stays; new values are formatted with the new type's `format_value`. Document as a known limitation.
- **Form duplicated:** the copy gets no `SheetSync` row (§6.3). The owner can connect a new sheet for the copy.

## 9. Errors, quotas, and limits

| Failure | Detection | Behaviour |
|---|---|---|
| Google 5xx / timeout | Sheets client exception | Retry with backoff (§7.3), submission unaffected |
| 429 / quota exceeded | HTTP 429 | Back off, reduce batch size, stop that run; do not hammer the API |
| 401, refresh succeeds | First call after expiry | Refresh once, retry the batch |
| 401 / `invalid_grant` | Refresh fails | Pause sync, mark account disconnected, owner must reconnect |
| 403, sheet not shared / permission removed | Sheets API error | Pause sync, surface error, keep queue 7 days |
| 404, spreadsheet deleted | Sheets API error | Pause sync, surface "spreadsheet deleted", keep queue 7 days |
| Spreadsheet renamed | — | No effect; DTOC stores the ID, not the title |
| Tab renamed by owner in Sheets | Next append fails / wrong tab | Worker checks the tab exists by title on each run; if missing, pause with "Responses tab not found — rename it back or disconnect". Do not auto-create a second tab silently. |
| Form closed / response limit reached | Existing `availability()` | No new submissions, so no new sync rows. Existing queue still drains. |
| Response deleted in DTOC | `DELETE /api/form/<id>/submission/<id>` | **v1: row stays in the Sheet.** Deleting a row in Sheets by position is unsafe once anyone has sorted / edited the Sheet. Document this. A later phase may add a "mark as deleted" column or a manual re-export. Do not silently delete Sheet rows in v1. |
| Janitor purges submissions (closed >90 days) | `janitor.py` | Sheet rows already written stay in Google. That is intentional: the Sheet is the creator's copy. The privacy page must say the 90-day purge does not delete the Google copy. |

### Quotas

Google Sheets API default quota is on the order of a few hundred requests per 100 seconds per project (verify the exact numbers in the Cloud Console at implementation time — do not hard-code a quota number from this document). Design mitigations, which are in the implementation checklist regardless of the exact number:

- Batch appends (up to 100 rows per call), one client per form per worker run, no per-submission API call in the web request.
- Exponential backoff on 429, with jitter.
- `Sync now` rate limit: once per minute per form.
- No polling of Sheets from the web request; status is read from `SheetSync` / `SyncQueue` in MariaDB.

## 10. Security and privacy

This is the section Copilot and the human reviewer should check hardest. A Google token that can edit a spreadsheet containing respondent data is a sensitive credential.

- **Least privilege:** only the two scopes in §5.2. No Drive full scope, no Gmail, no profile scope beyond the email needed for display.
- **Encryption at rest:** Fernet, key in envvars only (§5.4). No token in git, in logs, in the Flask session, in a URL, or in an error message shown to a user who is not the token owner.
- **Separation of identities:** Wikimedia username is the DTOC identity; Google email is display-only. Never use the Google email to grant DTOC access, and never use the Wikimedia username to grant Google access.
- **Audit:** every connect, disconnect, sheet create, pause, resume, and sync failure that pauses a form is an `AuditLog` row, with form ID and counts only.
- **Privacy page:** update `templates/privacy.html` *before* the feature ships, and bump its date. It must say, in plain language: (a) sync is opt-in per form, by the form owner; (b) responses are copied to a Google spreadsheet in the owner's Google account; (c) Google's privacy policy then applies to that copy; (d) the 90-day janitor purge (§9) does not delete the Google copy; (e) disconnecting stops future copies but does not delete the copy; (f) the owner's Google email is stored (encrypted token + display email) and deleted on disconnect.
- **Form notice:** when sync is active, the form view shows a short notice to respondents that responses are also stored in a Google Sheet. Exact wording to be agreed in implementation review; the notice must be present before the first synced submission, not added later.
- **Toolforge policy:** no third-party scripts in the browser (§4.2). Server-side calls to `googleapis.com` from Toolforge are outbound HTTPS from the tool account, like the existing Meta-Wiki API calls in `wiki_users.py`. Confirm outbound access to `*.googleapis.com` works from the Toolforge webservice before merging the implementation — if it is blocked, the implementation cannot ship as designed.
- **No respondent Google login:** respondents never see a Google prompt. If a future phase wants respondents to sign in with Google, that is a separate design and a separate privacy review.
- **Code review gates:** any change that touches token handling, scopes, or the Sheets client must be reviewed by a human (Shakil) as well as Copilot. Copilot review alone is not sufficient for token code.

## 11. Google verification — the non-code blocker

The two scopes in §5.2 are **sensitive scopes**. Google requires OAuth verification before the app can be used by the general public:

1. **Testing mode (first):** only test users listed in Cloud Console can connect. Use this for development and for a pilot with Shakil and 1–2 trusted form owners.
2. **Verification submission:** privacy policy URL (must be the live `/privacy` page, updated per §10), a YouTube demo video showing the OAuth flow and the Sheet being created, a written justification for each scope, and the app's homepage.
3. **Review time:** days to weeks, and Google may ask for changes to the consent wording or the privacy page. Budget for at least one round of changes.
4. **Until verification passes:** the feature ships behind `GOOGLE_SYNC_ENABLED=0` on Toolforge, or with a visible "beta — Google verification pending, test users only" notice. Do not present it as generally available while in Testing mode.

This is the most likely cause of delay, and it is not solved by writing more code. The implementation plan (§12) therefore puts the Cloud project and the verification submission in Phase 0, in parallel with the code, not after it.

## 12. Implementation plan

### Phase 0 — Setup (no code, can start today)

- [ ] Decide the Google Cloud project owner (Open Question 1).
- [ ] Create the project, enable Sheets + Drive APIs, create the OAuth client, set the redirect URIs (§5.1).
- [ ] Add Shakil as a test user; verify a manual OAuth flow with `curl` / OAuth Playground before writing any DTOC code.
- [ ] Confirm outbound HTTPS to `googleapis.com` works from Toolforge.
- [ ] Draft the privacy-page update (§10) and the respondent notice wording.

### Phase 1 — Connect + create + backfill (MVP)

- [ ] `config.py`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_KEY`, `GOOGLE_SYNC_ENABLED`.
- [ ] `requirements.txt`: the four packages in §4.2, pinned.
- [ ] `models.py`: `GoogleAccount`, `SheetSync`, `SyncQueue` + `ensure_schema_upgrades()` entries if columns are added later.
- [ ] `google_tokens.py`: Fernet encrypt / decrypt, with tests for round-trip, wrong-key failure, and empty-token rejection.
- [ ] `google_auth.py`: connect / callback / disconnect, `state` handling, HTTPS redirect, generic error messages, `AuditLog` entries.
- [ ] `google_sheets.py`: create spreadsheet, write header row, append batch. No Flask imports.
- [ ] Builder Settings UI: Sheets section, connect button, status badge, spreadsheet link. Hidden when `GOOGLE_SYNC_ENABLED=0`.
- [ ] `POST /form/<id>/sheets/connect`: idempotent create + header + backfill queue.
- [ ] `templates/privacy.html`: update + date bump.
- [ ] Tests: OAuth state mismatch, callback error, token encryption, header/row mapping reusing `column_headers` / `format_value`, idempotent connect, disconnect revokes + deletes.
- [ ] Manual test on Toolforge with a test Google account, behind the flag, with 1 form and 3 responses.

### Phase 2 — Live sync + worker

- [ ] `forms_api.view_form`: enqueue `SyncQueue` row after commit (§7.1). No Google call in the request.
- [ ] `sync_worker.py` + `setup_jobs.sh`: add a `toolforge-jobs` schedule (every 5 minutes), claim / batch / backoff / pause logic (§7.3).
- [ ] `POST /form/<id>/sheets/sync-now` + status endpoint (§6.2), with the once-per-minute rate limit.
- [ ] Dashboard / submissions page: status badge, last sync, pending count, last error, reconnect button.
- [ ] Tests: worker batching, retry backoff, 401 refresh, `invalid_grant` pause, duplicate-claim safety, backfill ordering.

### Phase 3 — Hardening and pilot

- [ ] Error table in §9 implemented and manually tested: revoke token, delete sheet, remove tab, rename tab, hit quota in a test project.
- [ ] Disconnect / pause / resume flows, including the 7-day queue expiry and the "row stays in Sheet on DTOC delete" notice.
- [ ] Submit Google verification (§11) with the demo video and the updated privacy page.
- [ ] Pilot with 2–3 real forms, with the owners' explicit agreement that this is a beta and that the Sheet is a copy.

### Phase 4 — Later (not in the implementation PR)

- Pick an existing spreadsheet (Drive picker, permission check, header compatibility check).
- Editor-initiated sync (owner opt-in).
- Column for "deleted in DTOC" / re-export, if owners ask for it.
- Per-form choice of tab name, or syncing to a second tab for analytics.
- Reconsider the service-account alternative only if per-user OAuth verification is refused — see §13.

### Rollback

- Set `GOOGLE_SYNC_ENABLED=0`: UI hidden, Google routes disabled, worker stops claiming rows. Existing `Submission` rows are untouched.
- Disconnect a form: stops its sync only. Disconnect an account: revokes its token and pauses all its forms' syncs.
- Full rollback of the code: revert the implementation PR; the new tables stay in MariaDB (empty / unused) until a later cleanup. Do not drop tables in a rollback — that would destroy the queue for in-flight syncs.

## 13. Alternatives considered

| Alternative | Why not v1 |
|---|---|
| **Service account** — creator shares their sheet with `dtoc-sync@….iam.gserviceaccount.com` | No Google consent screen and no verification, but the creator must share manually, the UX is confusing, and one shared service-account key is a single point of compromise for every form. Revisit only if OAuth verification fails. |
| **Manual CSV export → import into Sheets** | Already exists (`export.py`). Zero new code, but no live sync, which is the requested feature. Keep it as the fallback when sync is paused. |
| **Apps Script / webhook from DTOC to a Google Apps Script URL** | The creator would have to deploy and maintain a script, and DTOC would have to store a per-form webhook secret. More moving parts for the creator, weaker auth story. |
| **Zapier / Make / n8n** | Third-party automation, needs the creator to pay / maintain it, and respondent data would flow through another vendor. Out of scope for a Toolforge community tool. |
| **Store the Sheet ID in `schema.settings`** | Rejected in §6.3: settings are editor-editable and copied on duplication. Sync state belongs in its own table. |

## 14. Open questions for Shakil (owner decisions — implementation must not guess these)

1. **Google Cloud project owner:** personal Google account, or a Wikimedia Bangladesh / community Google account? Who pays (if anything) and who can access the Cloud Console if you are unavailable?
2. **Who can enable sync in v1:** form owner only (recommended in §5.5), or also editors?
3. **Existing sheet in v1:** is "create new spreadsheet" enough for the first release, or must creators be able to pick an existing sheet on day one?
4. **Response deletion:** confirm the v1 rule — a response deleted in DTOC stays in the Sheet. Is that acceptable, or should deletion be blocked / warned while sync is on?
5. **Pilot forms:** which 2–3 forms should pilot this, and are their owners happy to be test users for Google verification?
6. **Privacy wording:** who reviews the updated privacy page and the respondent notice — you, Wikimedia Bangladesh, or someone else?

## 15. Acceptance criteria (for the implementation PR)

- [ ] A form owner can connect Google, create a sheet, and see existing responses backfilled, with no Google prompt shown to respondents.
- [ ] A new submission appears as a new row, in export column order, within 5 minutes (worker interval) — and the respondent's 201 is unaffected if Google is down.
- [ ] Revoking the Google token pauses sync with a clear reconnect message; no response is lost from DTOC.
- [ ] Disconnect revokes the token at Google and deletes it from DTOC; the spreadsheet remains.
- [ ] No token, code, or answer data appears in logs, error messages, or `AuditLog.details`.
- [ ] `pytest` passes, including new tests for OAuth state, token encryption, header mapping, worker retries, and idempotent connect.
- [ ] Privacy page updated and dated; respondent notice visible on synced forms.
- [ ] `GOOGLE_SYNC_ENABLED=0` hides the feature completely.
- [ ] Google verification is at least submitted, or the PR states explicitly that the feature is test-users-only.

## References

- DTOC repo, `master` `3a7c215`: `app.py`, `auth.py`, `config.py`, `models.py`, `forms_api.py`, `form_schema.py`, `export.py`, `janitor.py`, `setup_jobs.sh`, `templates/privacy.html`, `templates/builder.html`
- Google Sheets API: https://developers.google.com/sheets/api
- Google Drive API (`drive.file` scope): https://developers.google.com/drive/api
- Google OAuth 2.0 for Web Server Applications: https://developers.google.com/identity/protocols/oauth2/web-server
- Google OAuth verification / sensitive scopes: https://support.google.com/cloud/answer/9110914
- Toolforge jobs: https://wikitech.wikimedia.org/wiki/Help:Toolforge/Jobs_framework
