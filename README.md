# DTOC Form-Collection Tool

A form-collection application built for Wikimedia Toolforge. 
Features OAuth integration, automated data retention policies, and customizable forms.

## Tech Stack
- Python (Flask)
- SQLite / MariaDB
- Jinja2 + Vanilla JS

## OAuth configuration
Set these environment variables for Wikimedia OAuth login:

- `WIKI_CLIENT_ID`
- `WIKI_CLIENT_SECRET`
- `WIKI_REDIRECT_URI` (optional, recommended on Toolforge; example: `https://dtoc.toolforge.org/oauth-callback`)
