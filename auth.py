from flask import Blueprint, session, redirect, request, url_for, flash
from mwoauth import ConsumerToken, Handshaker
import os
from config import Config

auth_bp = Blueprint('auth', __name__)

def get_handshaker():
    consumer_token = ConsumerToken(Config.WIKI_CLIENT_ID, Config.WIKI_CLIENT_SECRET)
    return Handshaker(Config.OAUTH_MWURI, consumer_token)

@auth_bp.route('/login')
def login():
    handshaker = get_handshaker()
    redirect_url, request_token = handshaker.initiate()
    session['request_token'] = dict(zip(request_token._fields, request_token))
    session['return_to'] = request.args.get('next', url_for('home'))
    return redirect(redirect_url)

@auth_bp.route('/oauth-callback')
def oauth_callback():
    if 'request_token' not in session:
        flash("OAuth flow failed. Missing request token.")
        return redirect(url_for('home'))
    
    handshaker = get_handshaker()
    request_token_dict = session['request_token']
    from mwoauth.tokens import RequestToken
    request_token = RequestToken(**request_token_dict)
    
    access_token = handshaker.complete(request_token, request.query_string)
    session['access_token'] = dict(zip(access_token._fields, access_token))
    
    identity = handshaker.identify(access_token)
    session['username'] = identity['username']
    
    return_to = session.pop('return_to', url_for('home'))
    return redirect(return_to)

@auth_bp.route('/logout')
def logout():
    session.pop('access_token', None)
    session.pop('username', None)
    return redirect(url_for('home'))
