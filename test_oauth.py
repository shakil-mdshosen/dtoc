import requests

CLIENT_ID = "ae071ef7591ed1771dbe77e5cdae49d5"
CLIENT_SECRET = "08dcd9b3e19195e2611261ebf5d4e09e68112f65"
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
