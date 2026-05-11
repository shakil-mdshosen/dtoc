from flask import Blueprint, session, redirect, request, url_for, flash
from requests_oauthlib import OAuth2Session
import os
from config import Config

auth_bp = Blueprint('auth', __name__)

AUTHORIZATION_BASE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/authorize'
TOKEN_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/access_token'
PROFILE_URL = 'https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile'

@auth_bp.route('/login')
def login():
    session['return_to'] = request.args.get('next', url_for('home'))
    
    redirect_uri = url_for('auth.oauth_callback', _external=True)
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://')
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, redirect_uri=redirect_uri)
    authorization_url, state = oauth.authorization_url(AUTHORIZATION_BASE_URL)
    
    session['oauth_state'] = state
    return redirect(authorization_url)

@auth_bp.route('/oauth-callback')
def oauth_callback():
    if 'oauth_state' not in session:
        flash("OAuth flow failed. Missing state.", "danger")
        return redirect(url_for('home'))
        
    redirect_uri = url_for('auth.oauth_callback', _external=True)
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://')
        
    oauth = OAuth2Session(Config.WIKI_CLIENT_ID, state=session['oauth_state'], redirect_uri=redirect_uri)
    
    try:
        authorization_response = request.url
        if authorization_response.startswith('http://'):
            authorization_response = authorization_response.replace('http://', 'https://')
            
        token = oauth.fetch_token(
            TOKEN_URL,
            authorization_response=authorization_response,
            client_secret=Config.WIKI_CLIENT_SECRET
        )
        session['access_token'] = token
        
        # Fetch user identity
        profile = oauth.get(PROFILE_URL).json()
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
