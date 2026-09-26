import json

import pytest
from sqlalchemy import create_engine, inspect, text

import auth
from app import create_app
from config import Config
from form_schema import sanitize_settings
from models import db, Form, Submission


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WIKI_CLIENT_ID = 'test-client'


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def login(client, username='Alice', email='alice@example.org'):
    with client.session_transaction() as session:
        session['username'] = username
        session['email'] = email
        session['email_checked'] = True


@pytest.fixture
def client(app):
    client = app.test_client()
    login(client)
    return client


def make_form(client, collect_email=True):
    schema = {
        'settings': {'collect_email': collect_email},
        'fields': [{'id': 'q1', 'type': 'text', 'label': 'Name', 'required': True}],
    }
    client.post('/builder', data={'title': 'Winners', 'schema': json.dumps(schema)})
    return Form.query.order_by(Form.id.desc()).first()


def test_collect_email_setting_defaults_off():
    assert sanitize_settings({})['collect_email'] is False
    assert sanitize_settings({'collect_email': True})['collect_email'] is True


def test_email_is_stored_and_disclosed_when_form_collects_it(app, client):
    form = make_form(client)
    html = client.get(f'/form/{form.id}').get_data(as_text=True)
    assert 'collects your confirmed Wikimedia email address' in html
    assert 'alice@example.org' in html

    assert client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}}).status_code == 201
    assert Submission.query.one().submitted_email == 'alice@example.org'


def test_email_is_not_stored_when_form_does_not_collect_it(app, client):
    form = make_form(client, collect_email=False)
    html = client.get(f'/form/{form.id}').get_data(as_text=True)
    assert 'collects your confirmed Wikimedia email' not in html
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})
    assert Submission.query.one().submitted_email is None


def test_user_without_confirmed_email_is_blocked(app, client):
    form = make_form(client)
    login(client, 'Bob', email=None)

    page = client.get(f'/form/{form.id}')
    html = page.get_data(as_text=True)
    assert page.status_code == 403
    assert 'requires a confirmed email address' in html
    assert 'Special:ChangeEmail' in html
    assert f'/login?next=/form/{form.id}' in html
    assert 'id="formRoot"' not in html

    response = client.post(f'/form/{form.id}', json={'answers': {'q1': 'Bob'}})
    assert response.status_code == 409
    assert Submission.query.count() == 0


def test_session_from_before_email_support_is_asked_to_log_in_again(app, client):
    form = make_form(client)
    with client.session_transaction() as session:
        session.pop('email')
        session.pop('email_checked')
    html = client.get(f'/form/{form.id}').get_data(as_text=True)
    assert 'Please log in again' in html


def test_results_and_exports_include_email(app, client):
    form = make_form(client)
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})

    html = client.get(f'/form/{form.id}/submissions').get_data(as_text=True)
    assert 'mailto:alice@example.org' in html

    csv_text = client.get(f'/form/{form.id}/export/csv').get_data(as_text=True)
    assert csv_text.splitlines()[0] == 'ID,Submitted At,Submitted By,Submitted Email,Name'
    assert 'alice@example.org' in csv_text.splitlines()[1]

    exported = json.loads(client.get(f'/form/{form.id}/export/json').get_data(as_text=True))
    assert exported[0]['submitted_email'] == 'alice@example.org'


def test_exports_without_email_keep_old_columns(app, client):
    form = make_form(client, collect_email=False)
    client.post(f'/form/{form.id}', json={'answers': {'q1': 'Alice'}})
    csv_text = client.get(f'/form/{form.id}/export/csv').get_data(as_text=True)
    assert csv_text.splitlines()[0] == 'ID,Submitted At,Submitted By,Name'
    assert 'submitted_email' not in client.get(f'/form/{form.id}/export/json').get_data(as_text=True)


class FakeResponse:
    ok = True
    status_code = 200
    text = ''

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.mark.parametrize('profile, expected', [
    ({'username': 'Bob', 'email': 'bob@example.org', 'confirmed_email': True}, 'bob@example.org'),
    ({'username': 'Bob', 'email': 'bob@example.org', 'confirmed_email': False}, None),
    ({'username': 'Bob'}, None),
])
def test_oauth_callback_keeps_only_confirmed_email(app, monkeypatch, profile, expected):
    class FakeOAuth:
        def __init__(self, *args, **kwargs):
            pass

        def authorization_url(self, url):
            return url + '?state=abc', 'abc'

        def get(self, *args, **kwargs):
            return FakeResponse(profile)

    monkeypatch.setattr(auth, 'OAuth2Session', FakeOAuth)
    monkeypatch.setattr('requests.post', lambda *a, **k: FakeResponse({'access_token': 't'}))
    client = app.test_client()
    client.get('/login')
    client.get('/login/wikimedia/callback?code=xyz&state=abc')
    with client.session_transaction() as session:
        assert session['username'] == 'Bob'
        assert session['email'] == expected
        assert session['email_checked'] is True


def test_existing_database_gets_email_column(tmp_path):
    path = tmp_path / 'old.db'
    engine = create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE submission (id INTEGER PRIMARY KEY, form_id INTEGER NOT NULL, '
                          'data TEXT NOT NULL, submitted_at DATETIME, submitted_by VARCHAR(255))'))
        conn.execute(text("INSERT INTO submission (form_id, data, submitted_by) VALUES (1, '{}', 'Old')"))

    class OldDbConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{path}'

    create_app(OldDbConfig)
    columns = {c['name'] for c in inspect(engine).get_columns('submission')}
    assert 'submitted_email' in columns
    with engine.connect() as conn:
        assert conn.execute(text('SELECT submitted_by, submitted_email FROM submission')).one() == ('Old', None)
