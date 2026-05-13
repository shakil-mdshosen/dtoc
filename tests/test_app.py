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
