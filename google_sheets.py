"""Google Sheets API client for DTOC sync.

No Flask imports in this module: it is a pure client wrapper that the
web blueprint (``google_auth.py``) and the background worker
(``sync_worker.py``) both use. All Google API errors are translated to
:class:`SheetsError` with a ``kind`` so callers can decide
retry / pause / reconnect without parsing Google error bodies —
and without logging row contents or tokens.

Google libraries are imported lazily inside the functions that need
them, so the app still imports and starts when the Google packages
are not installed and sync is disabled.
"""

import requests

from form_schema import column_headers, format_value

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# Exactly these two scopes. No identity/profile scope is requested in
# v1: the implementation does not fetch a Google profile, and
# GoogleAccount.google_email stays NULL (see google_auth.py).
SCOPES = [SHEETS_SCOPE, DRIVE_FILE_SCOPE]

SHEET_TITLE = "Responses"
USER_AGENT = "dtoc-toolforge/1.0 (https://dtoc.toolforge.org)"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Error kinds returned in SheetsError.kind
KIND_AUTH = "auth"            # 401 / invalid_grant: token revoked or expired
KIND_NOT_FOUND = "not_found"  # 404: spreadsheet or tab deleted
KIND_FORBIDDEN = "forbidden"  # 403: access removed
KIND_QUOTA = "quota"          # 429: quota exceeded
KIND_TRANSIENT = "transient"  # 5xx / timeout: retry with backoff
KIND_CONFIG = "config"        # Google libs / credentials not configured


class SheetsError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _classify_status(status):
    if status == 401:
        return KIND_AUTH
    if status == 403:
        return KIND_FORBIDDEN
    if status == 404:
        return KIND_NOT_FOUND
    if status == 429:
        return KIND_QUOTA
    if status is not None and status >= 500:
        return KIND_TRANSIENT
    return KIND_TRANSIENT


def _wrap_http_error(exc):
    """Translate a googleapiclient HttpError (or similar) to SheetsError."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status", None)
    return SheetsError(_classify_status(status), "Google Sheets request failed")


def build_service(credentials):
    """Build a Sheets v4 service. Lazy import; raises SheetsError(config)."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-api-python-client is not installed") from exc
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def credentials_from_refresh_token(refresh_token, client_id, client_secret):
    """Build Credentials from a decrypted refresh token and refresh it.

    Raises SheetsError(auth) on invalid_grant, SheetsError(transient)
    on other refresh failures. The caller must never log the token.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-auth is not installed") from exc
    creds = Credentials(
        token=None, refresh_token=refresh_token, token_uri=(
            "https://oauth2.googleapis.com/token"),
        client_id=client_id, client_secret=client_secret, scopes=SCOPES)
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        text = str(exc).lower()
        if "invalid_grant" in text or "invalid grant" in text:
            raise SheetsError(
                KIND_AUTH, "Google refresh token is no longer valid") from exc
        raise SheetsError(
            KIND_TRANSIENT, "Google token refresh failed") from exc
    except Exception as exc:  # network errors etc.
        raise SheetsError(
            KIND_TRANSIENT, "Google token refresh failed") from exc
    return creds


def revoke_refresh_token(refresh_token):
    """Revoke a refresh token at Google.

    Returns True on success. Returns False on a transient failure
    (network / 5xx) — the caller must keep the account row and show a
    retryable status, not claim disconnect succeeded. Never logs the
    token. A 400 (already revoked / invalid) counts as revoked.
    """
    try:
        resp = requests.post(
            REVOKE_URL, data={"token": refresh_token},
            headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException:
        return False
    return resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# Sheet format — same columns as the CSV/XLSX export.
# Meta columns mirror export._meta_headers/_meta_row (kept in sync by
# tests/test_google_sheets.py::test_sheet_headers_match_export); question
# columns reuse form_schema.column_headers/format_value directly.
# ---------------------------------------------------------------------------

def meta_headers(with_email):
    return ["ID", "Submitted At", "Submitted By"] + (
        ["Submitted Email"] if with_email else [])


def meta_row(submission, with_email):
    row = [submission.id, submission.submitted_at.isoformat(),
           submission.submitted_by]
    if with_email:
        row.append(submission.submitted_email or "")
    return row


def sheet_headers_for(schema, submissions_data, with_email):
    return meta_headers(with_email) + column_headers(schema, submissions_data)


def sheet_row(submission, data, headers, with_email):
    """One row, in the same order/format as export_csv builds it."""
    row = meta_row(submission, with_email)
    question_headers = headers[len(meta_headers(with_email)):]
    row.extend(format_value(data.get(h, "")) for h in question_headers)
    return ["" if v is None else v for v in row]


def create_spreadsheet(service, title):
    """Create a spreadsheet titled ``title``. Returns (id, url)."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-api-python-client is not installed") from exc
    body = {"properties": {"title": (title or "DTOC form")[:255]},
            "sheets": [{"properties": {"title": SHEET_TITLE}}]}
    try:
        result = service.spreadsheets().create(body=body).execute()
    except HttpError as exc:
        raise _wrap_http_error(exc) from exc
    except Exception as exc:
        raise SheetsError(KIND_TRANSIENT, "Google Sheets request failed") from exc
    return result.get("spreadsheetId"), result.get("spreadsheetUrl")


def get_sheet_titles(service, spreadsheet_id):
    """Titles of the tabs in a spreadsheet (to check 'Responses' exists)."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-api-python-client is not installed") from exc
    try:
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
        ).execute()
    except HttpError as exc:
        raise _wrap_http_error(exc) from exc
    except Exception as exc:
        raise SheetsError(KIND_TRANSIENT, "Google Sheets request failed") from exc
    return [s.get("properties", {}).get("title", "")
            for s in meta.get("sheets", [])]


def write_headers(service, spreadsheet_id, sheet_title, headers):
    """Write the header row to A1 of ``sheet_title``."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-api-python-client is not installed") from exc
    try:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_title}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()
    except HttpError as exc:
        raise _wrap_http_error(exc) from exc
    except Exception as exc:
        raise SheetsError(KIND_TRANSIENT, "Google Sheets request failed") from exc


def append_rows(service, spreadsheet_id, sheet_title, rows):
    """Append ``rows`` (list of lists) to ``sheet_title``.

    Returns the number of rows the API reports as updated. Raises
    SheetsError with a classified kind on failure.
    """
    if not rows:
        return 0
    try:
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise SheetsError(
            KIND_CONFIG, "google-api-python-client is not installed") from exc
    try:
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_title}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    except HttpError as exc:
        raise _wrap_http_error(exc) from exc
    except Exception as exc:
        raise SheetsError(KIND_TRANSIENT, "Google Sheets request failed") from exc
    updates = result.get("updates", {}) or {}
    return updates.get("updatedRows", len(rows))
