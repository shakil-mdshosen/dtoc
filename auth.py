from flask import Blueprint, session, redirect, request, url_for, flash
from requests_oauthlib import OAuth2Session
import os
from config import Config

auth_bp = Blueprint('auth', __name__)

AUTHORIZATION_BASE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/authorize'
TOKEN_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/access_token'
PROFILE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile'
USER_AGENT = 'dtoc-toolforge/1.0 (https://dtoc.toolforge.org)'

def _oauth_config_missing():
    return not Config.WIKI_CLIENT_ID or not Config.WIKI_CLIENT_SECRET

def _oauth_redirect_uri():
    redirect_uri = Config.WIKI_REDIRECT_URI or url_for('auth.oauth_callback', _external=True)
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://')
    return redirect_uri

@auth_bp.route('/login')
def login():
    if _oauth_config_missing():
        flash("OAuth is not configured. Please set WIKI_CLIENT_ID and WIKI_CLIENT_SECRET.", "danger")
        return redirect(url_for('home'))

    session['return_to'] = request.args.get('next', url_for('home'))
    redirect_uri = _oauth_redirect_uri()
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, redirect_uri=redirect_uri)
    authorization_url, state = oauth.authorization_url(AUTHORIZATION_BASE_URL)
    
    session['oauth_state'] = state
    return redirect(authorization_url)

@auth_bp.route('/oauth-callback')
def oauth_callback():
    if _oauth_config_missing():
        flash("OAuth is not configured. Please set WIKI_CLIENT_ID and WIKI_CLIENT_SECRET.", "danger")
        return redirect(url_for('home'))

    if 'oauth_state' not in session:
        flash("OAuth flow failed. Missing state.", "danger")
        return redirect(url_for('home'))
        
    redirect_uri = _oauth_redirect_uri()
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, state=session['oauth_state'], redirect_uri=redirect_uri)
    
    import requests
    try:
        code = request.args.get('code')
        if not code:
            flash("OAuth login failed: No code returned.", "danger")
            return redirect(url_for('home'))
            
        token_response = requests.post(TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': Config.WIKI_CLIENT_ID,
            'client_secret': Config.WIKI_CLIENT_SECRET
        },
           headers={'User-Agent': USER_AGENT})
        
        if not token_response.ok:
            flash(f"OAuth token exchange failed: {token_response.text}", "danger")
            return redirect(url_for('home'))
            
        token = token_response.json()
        session['access_token'] = token
        
        # Fetch user identity
        oauth_client = OAuth2Session(Config.WIKI_CLIENT_ID, token=token)
        profile = oauth_client.get(PROFILE_URL, headers={'User-Agent': USER_AGENT}).json()
        session['username'] = profile.get('username')
    except Exception as e:
        flash(f"OAuth login failed: {str(e)}", "danger")
        return redirect(url_for('home'))
        
    return_to = session.pop('return_to', url_for('home'))
    return redirect(return_to)

@auth_bp.route('/logout')
def logout():
    session.pop('access_token', None)
    session.pop('oauth_state', None)
    session.pop('username', None)
    return redirect(url_for('home'))
