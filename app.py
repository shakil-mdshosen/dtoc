from flask import Flask, render_template, session
from config import Config
from models import db, ensure_schema_upgrades

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    from auth import auth_bp
    from forms_api import forms_bp, is_owner_user
    from export import export_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(forms_bp)
    app.register_blueprint(export_bp)

    # Google Sheets sync is optional and fail-closed: the blueprint is
    # registered only when fully configured, and google_auth itself
    # refuses every flow when it is not. Importing it must never stop
    # the app starting (e.g. Google libs not installed).
    try:
        from google_auth import google_bp, google_sync_available
        if google_sync_available(app.config):
            app.register_blueprint(google_bp)
    except Exception:  # pragma: no cover - missing optional dependency
        app.logger.warning("Google Sheets sync unavailable; continuing "
                           "without it")

    @app.route('/')
    def home():
        return render_template('home.html')

    @app.route('/privacy')
    def privacy():
        return render_template('privacy.html')

    @app.context_processor
    def inject_owner_flag():
        from auth import login_url
        return {'is_owner': is_owner_user(session.get('username')), 'login_url': login_url}

    with app.app_context():
        db.create_all()
        ensure_schema_upgrades()

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
