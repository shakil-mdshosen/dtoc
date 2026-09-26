import json

import pytest

import auth
from app import create_app
from auth import safe_next_url
from config import Config
from models import db, Form


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WIKI_CLIENT_ID = 'test-client'


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        db.session.add(Form(title='Survey', schema=json.dumps({'fields': []}), created_by='Alice'))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_anonymous_form_visit_shows_login_warning_with_return_link(client):
    response = client.get('/form/1')
    html = response.get_data(as_text=True)

    assert response.status_code == 401
    assert 'You need to log in to access this form' in html
    assert '/login?next=/form/1' in html
    assert 'meta.wikimedia.org' not in response.headers.get('Location', '')


def test_query_string_is_kept_in_return_link(client):
    html = client.get('/dashboard?tab=closed').get_data(as_text=True)
    assert '/login?next=/dashboard?tab%3Dclosed' in html


def test_login_remembers_next_page(client):
    response = client.get('/login?next=/form/1')
    assert response.status_code == 302
    assert response.headers['Location'].startswith('https://meta.wikimedia.org/')
    with client.session_transaction() as session:
        assert session['return_to'] == '/form/1'


@pytest.mark.parametrize('target', [
    'https://evil.example/', '//evil.example/', '/\\evil.example', 'javascript:alert(1)', '', None,
])
def test_unsafe_next_values_fall_back_to_home(client, target):
    assert safe_next_url(target) is None
    client.get('/login', query_string={'next': target} if target is not None else {})
    with client.session_transaction() as session:
        assert session['return_to'] == '/'


def test_oauth_callback_returns_user_to_original_form(client, monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200
        text = ''

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeOAuth:
        def __init__(self, *args, **kwargs):
            pass

        def authorization_url(self, url):
            return url + '?state=abc', 'abc'

        def get(self, *args, **kwargs):
            return FakeResponse({'username': 'Bob'})

    monkeypatch.setattr(auth, 'OAuth2Session', FakeOAuth)
    monkeypatch.setattr('requests.post', lambda *a, **k: FakeResponse({'access_token': 't'}))

    client.get('/login?next=/form/1')
    response = client.get('/login/wikimedia/callback?code=xyz&state=abc')

    assert response.status_code == 302
    assert response.headers['Location'] == '/form/1'
    assert client.get('/form/1').status_code == 200


def test_expired_session_submission_gets_json_401_with_login_url(client):
    response = client.post('/form/1', json={'answers': {}},
                           headers={'Referer': 'http://localhost/form/1'})
    assert response.status_code == 401
    assert response.get_json()['login_url'] == '/login?next=/form/1'


def test_expired_session_form_post_returns_to_previous_page(client):
    response = client.post('/form/1/close', headers={'Referer': 'http://localhost/form/1/submissions'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/login?next=/form/1/submissions'
