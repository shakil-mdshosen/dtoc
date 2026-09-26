from app import create_app
from config import Config


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


def test_home_page_links_favicon():
    app = create_app(TestConfig)
    client = app.test_client()

    response = client.get('/')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'rel="icon"' in html
    assert '/static/favicon.svg' in html


def test_favicon_asset_is_served():
    app = create_app(TestConfig)
    client = app.test_client()

    response = client.get('/static/favicon.svg')

    assert response.status_code == 200
    assert 'image/svg+xml' in response.content_type


def test_privacy_notice_is_public_and_linked():
    app = create_app(TestConfig)
    client = app.test_client()

    response = client.get('/privacy')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'not operated by the Wikimedia Foundation' in html
    assert 'Cloud_Services_Terms_of_use' in html
    assert '90 days after a form is closed' in html

    assert 'href="/privacy"' in client.get('/').get_data(as_text=True)
    assert 'href="/privacy"' in client.get('/form/1').get_data(as_text=True)  # login-required page
