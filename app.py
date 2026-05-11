from flask import Flask, render_template
from config import Config
from models import db

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.config.get("WIKI_CLIENT_ID") or not app.config.get("WIKI_CLIENT_SECRET"):
        app.logger.warning("OAuth credentials missing: set WIKI_CLIENT_ID and WIKI_CLIENT_SECRET.")

    db.init_app(app)

    from auth import auth_bp
    from forms_api import forms_bp
    from export import export_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(forms_bp)
    app.register_blueprint(export_bp)

    @app.route('/')
    def home():
        return render_template('home.html')

    with app.app_context():
        db.create_all()

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
