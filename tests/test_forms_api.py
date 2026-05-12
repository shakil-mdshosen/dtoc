import base64

from forms_api import sanitize_header_image_url, HEADER_IMAGE_MAX_BYTES


def test_sanitize_header_image_url_accepts_https():
    assert sanitize_header_image_url('https://example.org/image.png') == 'https://example.org/image.png'


def test_sanitize_header_image_url_accepts_small_data_image():
    raw = b'fake-image-content'
    encoded = base64.b64encode(raw).decode('ascii')
    data_url = f'data:image/png;base64,{encoded}'
    assert sanitize_header_image_url(data_url) == data_url


def test_sanitize_header_image_url_rejects_oversized_data_image():
    raw = b'a' * (HEADER_IMAGE_MAX_BYTES + 1)
    encoded = base64.b64encode(raw).decode('ascii')
    assert sanitize_header_image_url(f'data:image/png;base64,{encoded}') == ''

