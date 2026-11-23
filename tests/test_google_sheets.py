"""Tests for Google Sheets sync (Phases 1-2).

All Google API calls are mocked. No live Google calls, no credentials,
no network. Tests that need a Fernet key generate a fresh one per run.
"""

import json
from datetime import datetime, timedelta
from unittest import mock

import pytest
from cryptography.fernet import Fernet

from app import create_app
from config import Config
from export import _meta_headers, _meta_row, includes_email
from form_schema import column_headers, format_value
from google_sheets import (SheetsError, sheet_headers_for, sheet_row)
from google_tokens import (GoogleTokenKeyError, decrypt_refresh_token,
                           encrypt_refresh_token, token_key_configured)
from models import (Form, GoogleAccount, Permission, SheetSync, Submission,
                    SyncQueue, db)

TOKEN_KEY = Fernet.generate_key().decode("ascii")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    GOOGLE_SYNC_ENABLED = True
    GOOGLE_CLIENT_ID = "test-google-client-id"
    GOOGLE_CLIENT_SECRET = "test-google-client-secret"
    GOOGLE_TOKEN_KEY = TOKEN_KEY


class DisabledConfig(TestConfig):
    GOOGLE_SYNC_ENABLED = False


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username="CreatorUser"):
    with client.session_transaction() as sess:
        sess["username"] = username
        sess["email"] = "creator@example.invalid"
        sess["email_checked"] = True


def make_form(username="CreatorUser", title="Feedback"):
    schema = {
        "fields": [
            {"id": "name", "key": "name", "label": "Name", "type": "text"},
            {"id": "rating", "key": "rating", "label": "Rating",
             "type": "number"},
            {"id": "likes", "key": "likes", "label": "Likes",
             "type": "checkbox", "options": ["A", "B"]},
        ],
        "settings": {"theme_color": "indigo"},
    }
    form = Form(title=title, schema=json.dumps(schema), created_by=username)
    db.session.add(form)
    db.session.commit()
    db.session.add(Permission(
        form_id=form.id, username=username, role="admin"))
    db.session.commit()
    return form


def make_submission(form, data=None, by="Respondent"):
    sub = Submission(
        form_id=form.id,
        data=json.dumps(data or {"Name": "Ana", "Rating": 5,
                                 "Likes": ["A", "B"]}),
        submitted_by=by)
    db.session.add(sub)
    db.session.commit()
    return sub


def make_account(username="CreatorUser"):
    account = GoogleAccount(
        wikimedia_username=username, google_email=None,
        encrypted_refresh_token=encrypt_refresh_token(
            "refresh-token-123", TOKEN_KEY),
        scopes="https://www.googleapis.com/auth/spreadsheets "
               "https://www.googleapis.com/auth/drive.file")
    db.session.add(account)
    db.session.commit()
    return account


def make_sync(form, username="CreatorUser"):
    sync = SheetSync(
        form_id=form.id, spreadsheet_id="sheet-abc",
        spreadsheet_url="https://docs.google.com/spreadsheets/d/sheet-abc",
        sheet_title="Responses", status="active",
        columns_json=json.dumps(
            ["ID", "Submitted At", "Submitted By", "name", "rating",
             "likes"]),
        created_by=username)
    db.session.add(sync)
    db.session.commit()
    return sync


# --- token encryption -------------------------------------------------------

def test_token_encryption_round_trip_and_wrong_key():
    ciphertext = encrypt_refresh_token("secret-refresh", TOKEN_KEY)
    assert ciphertext != "secret-refresh"
    assert decrypt_refresh_token(ciphertext, TOKEN_KEY) == "secret-refresh"
    other_key = Fernet.generate_key().decode("ascii")
    with pytest.raises(GoogleTokenKeyError):
        decrypt_refresh_token(ciphertext, other_key)
    with pytest.raises(GoogleTokenKeyError):
        decrypt_refresh_token(ciphertext, "not-a-fernet-key")


def test_token_key_configured():
    assert token_key_configured(TOKEN_KEY) is True
    assert token_key_configured("") is False
    assert token_key_configured("not-a-key") is False


# --- app starts with sync disabled ------------------------------------------

def test_app_starts_with_sync_disabled_and_google_routes_absent():
    app = create_app(DisabledConfig)
    with app.app_context():
        db.create_all()
    client = app.test_client()
    assert client.get("/google/connect").status_code == 404
    assert client.get("/").status_code == 200


# --- OAuth ------------------------------------------------------------------

def test_google_connect_redirects_to_google(client, app):
    login(client)
    resp = client.get("/google/connect")
    assert resp.status_code == 302
    assert "accounts.google.com/o/oauth2/v2/auth" in resp.location
    with client.session_transaction() as sess:
        assert sess.get("google_oauth_state")


def test_oauth_callback_state_mismatch_stores_nothing(client, app):
    login(client)
    with client.session_transaction() as sess:
        sess["google_oauth_state"] = "expected-state"
    resp = client.get("/google/callback?code=x&state=wrong-state")
    assert resp.status_code == 302
    with app.app_context():
        assert GoogleAccount.query.count() == 0


def test_oauth_callback_error_stores_nothing(client, app):
    login(client)
    with client.session_transaction() as sess:
        sess["google_oauth_state"] = "s"
    resp = client.get("/google/callback?error=access_denied&state=s")
    assert resp.status_code == 302
    with app.app_context():
        assert GoogleAccount.query.count() == 0


class _FakeTokenResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


def _callback(client, state="s", code="auth-code"):
    with client.session_transaction() as sess:
        sess["google_oauth_state"] = state
    return client.get(f"/google/callback?code={code}&state={state}")


def test_oauth_callback_without_refresh_token_stores_nothing(client, app):
    login(client)
    fake = _FakeTokenResponse({
        "access_token": "at-1", "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file"})
    with mock.patch("google_auth.requests.post", return_value=fake):
        resp = _callback(client)
    assert resp.status_code == 302
    with app.app_context():
        assert GoogleAccount.query.count() == 0


def test_oauth_callback_missing_scope_stores_nothing(client, app):
    login(client)
    fake = _FakeTokenResponse({
        "access_token": "at-1", "refresh_token": "rt-1",
        "scope": "https://www.googleapis.com/auth/spreadsheets"})
    with mock.patch("google_auth.requests.post", return_value=fake):
        _callback(client)
    with app.app_context():
        assert GoogleAccount.query.count() == 0


def test_oauth_callback_success_stores_encrypted_account(client, app):
    login(client)
    fake = _FakeTokenResponse({
        "access_token": "at-1", "refresh_token": "rt-1",
        "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file"})
    with mock.patch("google_auth.requests.post", return_value=fake):
        resp = _callback(client)
    assert resp.status_code == 302
    with app.app_context():
        account = GoogleAccount.query.filter_by(
            wikimedia_username="CreatorUser").first()
        assert account is not None
        # No Google profile is fetched in v1: email stays NULL.
        assert account.google_email is None
        assert account.encrypted_refresh_token != "rt-1"
        assert decrypt_refresh_token(
            account.encrypted_refresh_token, TOKEN_KEY) == "rt-1"
        assert "spreadsheets" in account.scopes
        assert "drive.file" in account.scopes


# --- headers / rows match export --------------------------------------------

def test_sheet_headers_and_rows_match_export(app):
    with app.app_context():
        form = make_form()
        sub = make_submission(form)
        from forms_api import load_data, load_schema
        schema = load_schema(form)
        datas = [load_data(sub)]
        with_email = includes_email(schema, [sub])
        headers = sheet_headers_for(schema, datas, with_email)
        expected = _meta_headers(with_email) + column_headers(schema, datas)
        assert headers == expected
        row = sheet_row(sub, datas[0], headers, with_email)
        expected_row = _meta_row(sub, with_email)
        expected_row += [format_value(datas[0].get(h, "")) for h in column_headers(schema, datas)]
        assert row == expected_row
        assert row[3] == "Ana"
        assert row[4] == 5
        assert row[5] == "A, B"


# --- submission enqueue ------------------------------------------------------

def test_submission_enqueues_when_sync_active(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
        form_id = form.id
    login(client, "Respondent")
    resp = client.post(f"/form/{form_id}",
                       json={"answers": {"name": "Ana", "rating": 5,
                                         "likes": ["A"]}})
    assert resp.status_code == 201
    with app.app_context():
        assert SyncQueue.query.count() == 1
        row = SyncQueue.query.first()
        assert row.status == "pending"


def test_submission_does_not_enqueue_without_sync(client, app):
    with app.app_context():
        form = make_form()
        form_id = form.id
    login(client, "Respondent")
    resp = client.post(f"/form/{form_id}",
                       json={"answers": {"name": "Ana"}})
    assert resp.status_code == 201
    with app.app_context():
        assert SyncQueue.query.count() == 0


# --- per-form connect / sync-now --------------------------------------------

def test_sheets_connect_is_idempotent_and_creator_only(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_submission(form)
        form_id = form.id
    login(client, "CreatorUser")
    with mock.patch("google_auth._service_for", return_value=object()), \
            mock.patch("google_auth.create_spreadsheet",
                       return_value=("sheet-abc", "https://example.invalid/sheet-abc")) as mock_create, \
            mock.patch("google_auth.write_headers", return_value=None):
        resp = client.post(f"/form/{form_id}/sheets/connect")
        assert resp.status_code == 302
        resp = client.post(f"/form/{form_id}/sheets/connect")
        assert resp.status_code == 302
        assert mock_create.call_count == 1
    with app.app_context():
        assert SheetSync.query.count() == 1
        # Backfill queued the existing submission.
        assert SyncQueue.query.count() == 1


def test_sheets_connect_rejects_non_creator(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        db.session.add(Permission(
            form_id=form.id, username="EditorUser", role="editor"))
        db.session.commit()
        form_id = form.id
    login(client, "EditorUser")
    resp = client.post(f"/form/{form_id}/sheets/connect")
    assert resp.status_code == 302
    with app.app_context():
        assert SheetSync.query.count() == 0


def test_sheets_status_json(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
        form_id = form.id
    login(client)
    resp = client.get(f"/form/{form_id}/sheets/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "active"
    assert data["is_creator"] is True
    assert data["spreadsheet_url"].endswith("sheet-abc")


def test_sync_now_rate_limited_and_creator_only(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
        make_submission(form)
        form_id = form.id
    login(client, "CreatorUser")
    resp = client.post(f"/form/{form_id}/sheets/sync-now",
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"queued for Google Sheets sync" in resp.data
    resp = client.post(f"/form/{form_id}/sheets/sync-now",
                       follow_redirects=True)
    assert b"Please wait a minute" in resp.data


# --- worker -------------------------------------------------------------------

def test_worker_sends_queued_rows(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        sync = make_sync(form)
        sub = make_submission(form)
        sub_id = sub.id
        row = SyncQueue(form_id=form.id, submission_id=sub.id)
        db.session.add(row)
        db.session.commit()
    from sync_worker import run_sync_worker
    with mock.patch("sync_worker._service_for", return_value=object()), \
            mock.patch("sync_worker.get_sheet_titles",
                       return_value=["Responses"]), \
            mock.patch("sync_worker.append_rows", return_value=1) as mock_append:
        result = run_sync_worker(app)
    assert result["sent"] == 1
    assert mock_append.call_count == 1
    with app.app_context():
        assert SyncQueue.query.first().status == "sent"
        sync = SheetSync.query.first()
        assert sync.last_synced_submission_id == sub_id


def test_worker_auth_failure_pauses_sync_and_retries(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
        sub = make_submission(form)
        db.session.add(SyncQueue(form_id=form.id, submission_id=sub.id))
        db.session.commit()
    from sync_worker import run_sync_worker
    with mock.patch("sync_worker._service_for", return_value=object()), \
            mock.patch("sync_worker.get_sheet_titles",
                       return_value=["Responses"]), \
            mock.patch("sync_worker.append_rows",
                       side_effect=SheetsError("auth", "bad token")):
        result = run_sync_worker(app)
    assert result["failed"] == 1
    with app.app_context():
        assert SyncQueue.query.first().status == "pending"
        assert SheetSync.query.first().status == "error"


def test_worker_reclaims_stale_sending_rows(app):
    from sync_worker import reclaim_stale_sending
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
        sub = make_submission(form)
        row = SyncQueue(form_id=form.id, submission_id=sub.id,
                        status="sending", attempts=1)
        db.session.add(row)
        db.session.commit()
        # Fake an old updated_at (lease is 30 minutes).
        row.updated_at = datetime.utcnow() - timedelta(minutes=31)
        db.session.commit()
        assert reclaim_stale_sending(datetime.utcnow()) == 1
        assert SyncQueue.query.first().status == "pending"


# --- disconnect ---------------------------------------------------------------

def test_account_disconnect_revoke_failure_keeps_account(client, app):
    with app.app_context():
        make_form()
        make_account()
    login(client)
    with mock.patch("google_auth.revoke_refresh_token", return_value=False):
        resp = client.post("/google/disconnect", follow_redirects=True)
    assert b"Could not reach Google" in resp.data
    with app.app_context():
        assert GoogleAccount.query.count() == 1


def test_account_disconnect_revokes_and_deletes(client, app):
    with app.app_context():
        form = make_form()
        make_account()
        make_sync(form)
    login(client)
    with mock.patch("google_auth.revoke_refresh_token", return_value=True):
        resp = client.post("/google/disconnect", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert GoogleAccount.query.count() == 0
        assert SheetSync.query.first().status == "disconnected"


# --- janitor ------------------------------------------------------------------

def test_janitor_skips_purge_with_unsent_queue_rows(app):
    from janitor import run_janitor
    with app.app_context():
        form = Form(title="Old", schema="{}", created_by="CreatorUser",
                    is_active=False,
                    closed_at=datetime.utcnow() - timedelta(days=95))
        db.session.add(form)
        db.session.commit()
        sub = make_submission(form)
        db.session.add(SyncQueue(form_id=form.id, submission_id=sub.id))
        db.session.commit()
        run_janitor(app)
        assert Submission.query.filter_by(form_id=form.id).count() == 1
        # Now mark the queue row sent: the purge may proceed.
        SyncQueue.query.first().status = "sent"
        db.session.commit()
        run_janitor(app)
        assert Submission.query.filter_by(form_id=form.id).count() == 0
        assert SyncQueue.query.filter_by(form_id=form.id).count() == 0
