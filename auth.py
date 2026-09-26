from flask import Blueprint, session, redirect, request, url_for, flash
from requests_oauthlib import OAuth2Session
from urllib.parse import urlparse
from config import Config

auth_bp = Blueprint('auth', __name__)

AUTHORIZATION_BASE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/authorize'
TOKEN_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/access_token'
PROFILE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile'
USER_AGENT = 'dtoc-toolforge/1.0 (https://dtoc.toolforge.org)'

def safe_next_url(target):
    """Return ``target`` if it is a path on this site, otherwise ``None``.

    Only relative paths such as ``/form/12`` are accepted so the login flow
    can't be abused as an open redirect to another website.
    """
    if not isinstance(target, str):
        return None
    target = target.strip()
    if not target.startswith('/') or target.startswith('//') or '\\' in target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


def current_path():
    """The path and query string of the current request, e.g. ``/form/12?x=1``."""
    path = request.full_path
    return path[:-1] if path.endswith('?') else path


def login_url(next_path=None):
    """URL of the login route that brings the user back to ``next_path`` afterwards."""
    next_path = safe_next_url(next_path or current_path())
    if not next_path or next_path.startswith(url_for('auth.login')):
        return url_for('auth.login')
    return url_for('auth.login', next=next_path)


@auth_bp.route('/login')
def login():
    session['return_to'] = safe_next_url(request.args.get('next')) or url_for('home')
    
    redirect_uri = url_for('auth.oauth_callback', _external=True)
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://')
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, redirect_uri=redirect_uri)
    authorization_url, state = oauth.authorization_url(AUTHORIZATION_BASE_URL)
    
    session['oauth_state'] = state
    return redirect(authorization_url)

@auth_bp.route('/login/wikimedia/callback')
def oauth_callback():
    if 'oauth_state' not in session:
        flash("OAuth flow failed. Missing state.", "danger")
        return redirect(url_for('home'))
        
    redirect_uri = url_for('auth.oauth_callback', _external=True)
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://')
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, state=session['oauth_state'], redirect_uri=redirect_uri)
    
    import requests
    try:
        code = request.args.get('code')
        if not code:
            flash("OAuth login failed: No code returned.", "danger")
            return redirect(url_for('home'))
            
        print(f"DEBUG: Starting POST to {TOKEN_URL}")
        token_response = requests.post(TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri
        }, auth=(Config.WIKI_CLIENT_ID, Config.WIKI_CLIENT_SECRET),
           headers={'User-Agent': USER_AGENT}, timeout=10)
        
        print(f"DEBUG: Token response status: {token_response.status_code}")
        
        if not token_response.ok:
            flash(f"OAuth token exchange failed: {token_response.text[:200]}...", "danger")
            return redirect(url_for('home'))
            
        token = token_response.json()
        print("DEBUG: Successfully parsed token JSON. Fetching profile...")
        
        # Clear out any potential old junk from session
        session.pop('access_token', None)
        session.pop('request_token', None)
        
        # Fetch user identity using the token directly (without saving it to session)
        oauth_client = OAuth2Session(Config.WIKI_CLIENT_ID, token=token)
        profile_response = oauth_client.get(PROFILE_URL, headers={'User-Agent': USER_AGENT}, timeout=10)
        print(f"DEBUG: Profile response status: {profile_response.status_code}")
        
        profile = profile_response.json()
        session['username'] = profile.get('username')
        # The consumer's grant includes email access. Keep the address only if
        # the user has confirmed it; forms that collect emails require one.
        email = (profile.get('email') or '').strip()
        session['email'] = email if email and profile.get('confirmed_email') else None
        session['email_checked'] = True
        print(f"DEBUG: Successfully logged in user {session['username']}")
        
    except requests.exceptions.Timeout:
        print("DEBUG: Caught requests.exceptions.Timeout")
        flash("OAuth login failed: Connection to Wikimedia timed out. This may be a Toolforge networking issue.", "danger")
        return redirect(url_for('home'))
    except Exception as e:
        print(f"DEBUG: Caught Exception: {e}")
        flash(f"OAuth login failed: {str(e)[:200]}", "danger")
        return redirect(url_for('home'))
        
    return_to = safe_next_url(session.pop('return_to', None)) or url_for('home')
    return redirect(return_to)

@auth_bp.route('/logout')
def logout():
    session.pop('access_token', None)
    session.pop('oauth_state', None)
    session.pop('username', None)
    session.pop('email', None)
    session.pop('email_checked', None)
    return redirect(url_for('home'))
