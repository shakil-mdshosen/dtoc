import requests

import os

CLIENT_ID = os.environ.get("WIKI_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("WIKI_CLIENT_SECRET", "")
TOKEN_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/access_token'

def test_auth():
    headers = {'User-Agent': 'dtoc-toolforge/1.0 (https://dtoc.toolforge.org)'}
    print("Testing with POST body...")
    resp1 = requests.post(TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'code': 'dummy_code',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': 'https://dtoc.toolforge.org/oauth-callback'
    }, headers=headers)
    print("POST body response:", resp1.status_code, resp1.text)

    print("\nTesting with Basic Auth...")
    resp2 = requests.post(TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'code': 'dummy_code',
        'redirect_uri': 'https://dtoc.toolforge.org/oauth-callback'
    }, auth=(CLIENT_ID, CLIENT_SECRET), headers=headers)
    print("Basic Auth response:", resp2.status_code, resp2.text)

if __name__ == "__main__":
    test_auth()
