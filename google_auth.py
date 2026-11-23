"""Google Sheets sync: OAuth flow and per-form sync routes.

Second, independent OAuth flow (the Wikimedia login in ``auth.py`` is
unaffected). The Wikimedia username is the DTOC identity; the Google
account is linked only by that username. No Google profile is fetched
in v1, so no identity scope is requested and
``GoogleAccount.google_email`` stays NULL — the two requested scopes
are exactly ``google_sheets.SCOPES``.

Security rules, all testable (see tests/test_google_sheets.py):
- ``state`` is fresh, random, single-use, compared in constant time.
- ``redirect_uri`` is exactly the registered URI, forced to HTTPS in
  production (same pattern as ``auth.py``).
- The authorisation ``code`` is exchanged once, server-side, and is
  never logged, stored, or put in a URL. Token responses are never
  logged; only the event name, form ID and HTTP status are logged.
- If Google returns no refresh token, the account is NOT stored as
  connected. The granted scopes returned by Google are stored and
  verified against the required scopes before sync is enabled.
- Disconnect (account): revoke first; a revoke/network failure keeps
  the account row and shows a retryable status — it must never
  silently claim disconnect succeeded.
- Form sync actions are creator-only in v1: ``Form.created_by`` must
  equal ``session['username']``. This is stricter than
  ``can_manage``/``can_edit`` (Copilot review of the design).
"""

import hmac
import json
import secrets
from datetime import datetime, timedelta

import requests
from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from requests_oauthlib import OAuth2Session

from auth import safe_next_url
from export import includes_email
from form_schema import column_headers
from forms_api import get_permission, load_data, load_schema, login_required
from google_sheets import (SCOPES, SHEET_TITLE, SheetsError,
                           build_service, create_spreadsheet,
                           credentials_from_refresh_token,
                           revoke_refresh_token, sheet_headers_for,
                           sheet_row, write_headers)
from google_tokens import (GoogleTokenKeyError, GoogleTokenUnavailable,
                           decrypt_refresh_token, encrypt_refresh_token,
                           token_key_configured)
from models import (AuditLog, Form, GoogleAccount, SheetSync, Submission,
                    SyncQueue, db)

google_bp = Blueprint("google", __name__)

AUTHORIZATION_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USER_AGENT = "dtoc-toolforge/1.0 (https://dtoc.toolforge.org)"
SYNC_NOW_MIN_SECONDS = 60
BACKFILL_BATCH = 500


def google_sync_available(config=None):
    """Fail closed: sync is available only when fully configured.

    Enabled flag + client ID/secret + a valid Fernet token key. If any
    of those is missing/invalid, the feature is off — the app still
    starts and every Google route refuses with a generic message.
    ``config`` may be passed explicitly (app factory, worker); inside
    a request it defaults to the current app's config.
    """
    if config is None:
        config = current_app.config
    if not config.get("GOOGLE_SYNC_ENABLED"):
        return False
    if not config.get("GOOGLE_CLIENT_ID") or not config.get("GOOGLE_CLIENT_SECRET"):
        return False
    return token_key_configured(config.get("GOOGLE_TOKEN_KEY"))


def _redirect_uri():
    uri = url_for("google.google_callback", _external=True)
    if uri.startswith("http://"):
        uri = uri.replace("http://", "https://", 1)
    return uri


def is_form_creator(form):
    """Strict v1 rule: the form's creator, not any collaborator/admin."""
    username = (session.get("username") or "").strip()
    return bool(username) and form.created_by == username


def _require_available():
    if google_sync_available():
        return None
    flash("Google Sheets sync is not enabled on this instance.", "warning")
    return redirect(url_for("forms.dashboard"))


def _creator_or_redirect(form):
    if is_form_creator(form):
        return None
    flash("Only the form creator can manage Google Sheets sync.", "danger")
    return redirect(url_for("forms.view_submissions", form_id=form.id))


def _account_for(username):
    return GoogleAccount.query.filter_by(
        wikimedia_username=username).first()


def _service_for(username):
    """Build a Sheets service for a user. Raises SheetsError."""
    account = _account_for(username)
    if not account:
        raise SheetsError("auth", "No Google account connected")
    try:
        refresh_token = decrypt_refresh_token(
            account.encrypted_refresh_token,
            current_app.config.get("GOOGLE_TOKEN_KEY"))
    except (GoogleTokenKeyError, GoogleTokenUnavailable) as exc:
        raise SheetsError("auth", "Stored Google token cannot be used") from exc
    creds = credentials_from_refresh_token(
        refresh_token, current_app.config.get("GOOGLE_CLIENT_ID"),
        current_app.config.get("GOOGLE_CLIENT_SECRET"))
    return build_service(creds)


def _sync_status_dict(form):
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    account = _account_for(session.get("username"))
    pending = 0
    if sync:
        pending = SyncQueue.query.filter_by(
            form_id=form.id, status="pending").count()
        pending += SyncQueue.query.filter_by(
            form_id=form.id, status="sending").count()
    return {
        "enabled": google_sync_available(),
        "google_connected": bool(account),
        "is_creator": is_form_creator(form),
        "status": sync.status if sync else "not_connected",
        "spreadsheet_id": sync.spreadsheet_id if sync else None,
        "spreadsheet_url": sync.spreadsheet_url if sync else None,
        "sheet_title": sync.sheet_title if sync else SHEET_TITLE,
        "last_synced_at": (
            sync.last_synced_at.isoformat() if sync and sync.last_synced_at
            else None),
        "pending_count": pending,
        "last_error": sync.last_error if sync else None,
    }


def _headers_for(form, schema):
    submissions = Submission.query.filter_by(form_id=form.id).order_by(
        Submission.submitted_at).all()
    datas = [load_data(s) for s in submissions]
    with_email = includes_email(schema, submissions)
    return sheet_headers_for(schema, datas, with_email), with_email


def _enqueue_missing(form, limit=None):
    """Queue submissions that have no queue row (or a failed one).

    Returns the number queued. Used by connect backfill and sync-now.
    """
    queued = 0
    query = Submission.query.filter_by(form_id=form.id).order_by(
        Submission.id)
    for sub in query.all():
        if limit is not None and queued >= limit:
            break
        row = SyncQueue.query.filter_by(submission_id=sub.id).first()
        if row is None:
            db.session.add(SyncQueue(
                form_id=form.id, submission_id=sub.id))
            queued += 1
        elif row.status in ("failed", "skipped"):
            row.status = "pending"
            row.next_retry_at = None
            row.last_error = None
            queued += 1
    if queued:
        db.session.commit()
    return queued


# ---------------------------------------------------------------------------
# Google account OAuth
# ---------------------------------------------------------------------------

@google_bp.route("/google/connect")
@login_required
def google_connect():
    unavailable = _require_available()
    if unavailable:
        return unavailable
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    session["google_return_to"] = (
        safe_next_url(request.args.get("next"))
        or url_for("forms.dashboard"))
    oauth = OAuth2Session(
        current_app.config.get("GOOGLE_CLIENT_ID"), scope=SCOPES,
        redirect_uri=_redirect_uri())
    authorization_url, _state = oauth.authorization_url(
        AUTHORIZATION_BASE_URL,
        access_type="offline", prompt="consent")
    # OAuth2Session returns its own state; keep the one we stored so the
    # callback compares against a value we generated and single-use.
    session["google_oauth_state"] = _state
    return redirect(authorization_url)


@google_bp.route("/google/callback")
@login_required
def google_callback():
    unavailable = _require_available()
    if unavailable:
        return unavailable
    return_to = safe_next_url(session.pop("google_return_to", None)) or \
        url_for("forms.dashboard")
    expected_state = session.pop("google_oauth_state", None)
    state = request.args.get("state", "")
    if not expected_state or not hmac.compare_digest(expected_state, state):
        flash("Google sign-in failed: please try again.", "danger")
        return redirect(return_to)
    if request.args.get("error"):
        flash("Google sign-in was cancelled.", "warning")
        return redirect(return_to)
    code = request.args.get("code")
    if not code:
        flash("Google sign-in failed: please try again.", "danger")
        return redirect(return_to)
    try:
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": _redirect_uri()},
            auth=(current_app.config.get("GOOGLE_CLIENT_ID"),
                  current_app.config.get("GOOGLE_CLIENT_SECRET")),
            headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException:
        flash("Google sign-in failed: please try again.", "danger")
        return redirect(return_to)
    if not resp.ok:
        current_app.logger.warning(
            "google oauth token exchange failed: status=%s", resp.status_code)
        flash("Google sign-in failed: please try again.", "danger")
        return redirect(return_to)
    token = resp.json()
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        # Do NOT persist an access-only account as "connected".
        flash("Google did not return a refresh token. Please reconnect "
              "and make sure you consent when Google asks.", "danger")
        return redirect(return_to)
    granted = set((token.get("scope") or "").split())
    if not set(SCOPES).issubset(granted):
        flash("Google did not grant all the permissions needed. "
              "Please reconnect and accept them.", "danger")
        return redirect(return_to)
    try:
        encrypted = encrypt_refresh_token(
            refresh_token, current_app.config.get("GOOGLE_TOKEN_KEY"))
    except (GoogleTokenKeyError, GoogleTokenUnavailable):
        flash("Google Sheets sync is not configured correctly. "
              "Please contact the tool maintainer.", "danger")
        return redirect(return_to)
    username = session.get("username")
    account = _account_for(username)
    if account is None:
        account = GoogleAccount(
            wikimedia_username=username, google_email=None,
            encrypted_refresh_token=encrypted,
            scopes=" ".join(sorted(granted)))
        db.session.add(account)
    else:
        account.encrypted_refresh_token = encrypted
        account.scopes = " ".join(sorted(granted))
        account.google_email = None
    db.session.add(AuditLog(
        action="GOOGLE_CONNECT",
        details=f"Google account connected by {username}"))
    db.session.commit()
    flash("Google account connected. You can now sync a form to a "
          "Google Sheet.", "success")
    return redirect(return_to)


@google_bp.route("/google/disconnect", methods=["POST"])
@login_required
def google_disconnect():
    unavailable = _require_available()
    if unavailable:
        return unavailable
    username = session.get("username")
    account = _account_for(username)
    if not account:
        flash("No Google account is connected.", "info")
        return redirect(url_for("forms.dashboard"))
    try:
        refresh_token = decrypt_refresh_token(
            account.encrypted_refresh_token,
            current_app.config.get("GOOGLE_TOKEN_KEY"))
    except (GoogleTokenKeyError, GoogleTokenUnavailable):
        # We cannot revoke a token we cannot decrypt: keep the row so
        # the operator can retry after fixing GOOGLE_TOKEN_KEY.
        flash("Could not revoke the Google token right now. Your Google "
              "account is still connected — please try again later.",
              "danger")
        return redirect(url_for("forms.dashboard"))
    if not revoke_refresh_token(refresh_token):
        db.session.add(AuditLog(
            action="GOOGLE_DISCONNECT_FAILED",
            details=f"Google revoke failed for {username}; account kept"))
        db.session.commit()
        flash("Could not reach Google to revoke access. Your Google "
              "account is still connected — please try again.", "danger")
        return redirect(url_for("forms.dashboard"))
    # Revoke succeeded: delete the credential and pause its syncs, in
    # one transaction. Spreadsheets already created stay in Drive.
    SheetSync.query.filter_by(created_by=username).update(
        {"status": "disconnected"})
    db.session.add(AuditLog(
        action="GOOGLE_DISCONNECT",
        details=f"Google account disconnected by {username}"))
    db.session.delete(account)
    db.session.commit()
    flash("Google account disconnected. Existing spreadsheets were not "
          "deleted; they will no longer receive new responses.", "success")
    return redirect(url_for("forms.dashboard"))


# ---------------------------------------------------------------------------
# Per-form sync (creator-only)
# ---------------------------------------------------------------------------

@google_bp.route("/form/<int:form_id>/sheets/connect", methods=["POST"])
@login_required
def sheets_connect(form_id):
    unavailable = _require_available()
    if unavailable:
        return unavailable
    form = Form.query.get_or_404(form_id)
    denied = _creator_or_redirect(form)
    if denied:
        return denied
    account = _account_for(session.get("username"))
    if not account:
        return redirect(url_for(
            "google.google_connect",
            next=url_for("forms.view_submissions", form_id=form.id)))
    existing = SheetSync.query.filter_by(form_id=form.id).first()
    if existing and existing.status == "active":
        flash("This form is already synced to a Google Sheet.", "info")
        return redirect(url_for("forms.view_submissions", form_id=form.id))
    if existing and existing.status == "disconnected":
        db.session.delete(existing)
        db.session.commit()
    schema = load_schema(form)
    try:
        service = _service_for(session.get("username"))
        spreadsheet_id, spreadsheet_url = create_spreadsheet(
            service, form.title)
        headers, _with_email = _headers_for(form, schema)
        write_headers(service, spreadsheet_id, SHEET_TITLE, headers)
    except SheetsError as exc:
        current_app.logger.warning(
            "google sheets create failed: form=%s kind=%s", form.id, exc.kind)
        flash("Could not create the Google Sheet. Please try again.",
              "danger")
        return redirect(url_for("forms.view_submissions", form_id=form.id))
    sync = SheetSync(
        form_id=form.id, spreadsheet_id=spreadsheet_id,
        spreadsheet_url=spreadsheet_url, sheet_title=SHEET_TITLE,
        status="active", columns_json=json.dumps(headers),
        created_by=session.get("username"))
    db.session.add(sync)
    db.session.commit()
    # Backfill in a bounded batch in this request; the worker's
    # backfill sweep (sync_worker.py) queues the remainder.
    _enqueue_missing(form, limit=BACKFILL_BATCH)
    db.session.add(AuditLog(
        action="SHEET_CREATE",
        details=f"Form {form.id} synced to a Google Sheet by "
                f"{session.get('username')}"))
    db.session.commit()
    flash("Google Sheet created. Responses will be copied to it within "
          "a few minutes.", "success")
    return redirect(url_for("forms.view_submissions", form_id=form.id))


@google_bp.route("/form/<int:form_id>/sheets/disconnect", methods=["POST"])
@login_required
def sheets_disconnect(form_id):
    unavailable = _require_available()
    if unavailable:
        return unavailable
    form = Form.query.get_or_404(form_id)
    denied = _creator_or_redirect(form)
    if denied:
        return denied
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    if sync:
        sync.status = "disconnected"
        db.session.add(AuditLog(
            action="SHEET_SYNC_PAUSED",
            details=f"Form {form.id} Google Sheet disconnected by "
                    f"{session.get('username')}"))
        db.session.commit()
    flash("Google Sheets sync stopped for this form. The spreadsheet "
          "was not deleted.", "info")
    return redirect(url_for("forms.view_submissions", form_id=form.id))


@google_bp.route("/form/<int:form_id>/sheets/pause", methods=["POST"])
@login_required
def sheets_pause(form_id):
    unavailable = _require_available()
    if unavailable:
        return unavailable
    form = Form.query.get_or_404(form_id)
    denied = _creator_or_redirect(form)
    if denied:
        return denied
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    if sync and sync.status == "active":
        sync.status = "paused"
        db.session.add(AuditLog(
            action="SHEET_SYNC_PAUSED",
            details=f"Form {form.id} Google Sheet paused by "
                    f"{session.get('username')}"))
        db.session.commit()
    return redirect(url_for("forms.view_submissions", form_id=form.id))


@google_bp.route("/form/<int:form_id>/sheets/resume", methods=["POST"])
@login_required
def sheets_resume(form_id):
    unavailable = _require_available()
    if unavailable:
        return unavailable
    form = Form.query.get_or_404(form_id)
    denied = _creator_or_redirect(form)
    if denied:
        return denied
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    if sync and sync.status in ("paused", "error"):
        sync.status = "active"
        sync.last_error = None
        db.session.add(AuditLog(
            action="SHEET_SYNC_RESUMED",
            details=f"Form {form.id} Google Sheet resumed by "
                    f"{session.get('username')}"))
        db.session.commit()
    return redirect(url_for("forms.view_submissions", form_id=form.id))


@google_bp.route("/form/<int:form_id>/sheets/sync-now", methods=["POST"])
@login_required
def sheets_sync_now(form_id):
    unavailable = _require_available()
    if unavailable:
        return unavailable
    form = Form.query.get_or_404(form_id)
    denied = _creator_or_redirect(form)
    if denied:
        return denied
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    if not sync or sync.status != "active":
        flash("Google Sheets sync is not active for this form.", "warning")
        return redirect(url_for("forms.view_submissions", form_id=form.id))
    key = f"sheets_sync_now_{form_id}"
    last = session.get(key)
    now = datetime.utcnow()
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            last_dt = None
        if last_dt and (now - last_dt).total_seconds() < SYNC_NOW_MIN_SECONDS:
            flash("Sync was just requested. Please wait a minute before "
                  "trying again.", "warning")
            return redirect(url_for("forms.view_submissions", form_id=form.id))
    session[key] = now.isoformat()
    # Enqueue only; the worker does the Google calls. No Google call is
    # made in this request, so it cannot time out on a large backfill.
    queued = _enqueue_missing(form, limit=BACKFILL_BATCH)
    flash(f"{queued} response(s) queued for Google Sheets sync. The "
          "worker normally copies them within a few minutes.", "success")
    return redirect(url_for("forms.view_submissions", form_id=form.id))


@google_bp.route("/form/<int:form_id>/sheets/status")
@login_required
def sheets_status(form_id):
    form = Form.query.get_or_404(form_id)
    if not google_sync_available():
        return jsonify({"status": "disabled"}), 404
    perm = get_permission(form.id)
    if not perm and not is_form_creator(form):
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify(_sync_status_dict(form))


def sheet_row_for_submission(form, submission):
    """Build one Sheet row for a submission (worker use).

    Reuses the export helpers: meta columns + question keys, formatted
    with format_value, in the frozen SheetSync column order.
    """
    from forms_api import load_data as _load_data
    schema = load_schema(form)
    sync = SheetSync.query.filter_by(form_id=form.id).first()
    headers = json.loads(sync.columns_json) if sync else None
    if not headers:
        submissions = Submission.query.filter_by(
            form_id=form.id).order_by(Submission.submitted_at).all()
        datas = [_load_data(s) for s in submissions]
        headers, _ = _headers_for(form, schema)
    with_email = "Submitted Email" in headers
    data = load_data(submission)
    return sheet_row(submission, data, headers, with_email)
