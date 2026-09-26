"""Look up Wikimedia (global SUL) accounts on Meta-Wiki.

Used to suggest usernames when adding collaborators and to check that an
account exists before granting it access. Requests are made server-side so
the browser never talks to another site, and results are cached briefly.
"""
import time

import requests

META_API = 'https://meta.wikimedia.org/w/api.php'
USER_AGENT = 'dtoc-toolforge/1.0 (https://dtoc.toolforge.org)'
TIMEOUT = 5
CACHE_TTL = 300
MAX_USERNAME = 255

_cache = {}


class LookupUnavailable(Exception):
    """Meta-Wiki could not be reached or returned an unexpected response."""


def normalize_username(name):
    """Canonical MediaWiki form: underscores as spaces, single spaces, first letter upper-case."""
    if not isinstance(name, str):
        return ''
    name = ' '.join(name.replace('_', ' ').split())[:MAX_USERNAME]
    if name.lower().startswith('user:'):
        name = name[5:].strip()
    return name[:1].upper() + name[1:] if name else ''


def _api(params):
    key = tuple(sorted(params.items()))
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        response = requests.get(
            META_API,
            params=dict(params, action='query', format='json', formatversion='2'),
            headers={'User-Agent': USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise LookupUnavailable(str(exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get('query'), dict):
        raise LookupUnavailable('Unexpected response from Meta-Wiki')
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time() + CACHE_TTL, data['query'])
    return data['query']


def _flag(obj, key):
    """MediaWiki boolean flags may be ``true``, ``""`` or absent depending on the API format."""
    return key in obj and obj[key] is not False


def search_users(prefix, limit=8):
    """Global account names starting with ``prefix`` (locked accounts excluded)."""
    prefix = normalize_username(prefix)
    if len(prefix) < 2:
        return []
    query = _api({'list': 'globalallusers', 'aguprefix': prefix, 'agulimit': str(limit + 4), 'aguprop': 'lockinfo'})
    names = [u['name'] for u in query.get('globalallusers', [])
             if isinstance(u, dict) and u.get('name') and not _flag(u, 'locked')]
    return names[:limit]


def user_exists(name):
    """True if a global account with exactly this name exists and isn't locked.

    Uses ``list=globalallusers`` bounded to the exact name, because that module
    documents ``aguprop=lockinfo`` for reporting locked accounts.
    """
    name = normalize_username(name)
    if not name:
        return False
    query = _api({'list': 'globalallusers', 'agufrom': name, 'aguto': name,
                  'agulimit': '1', 'aguprop': 'lockinfo'})
    for user in query.get('globalallusers', []):
        if isinstance(user, dict) and user.get('name') == name:
            return not _flag(user, 'locked')
    return False
