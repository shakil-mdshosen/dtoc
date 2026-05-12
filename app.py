from flask import Flask, render_template, session
from config import Config
from models import db

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

    @app.route('/')
    def home():
        return render_template('home.html')

    @app.context_processor
    def inject_owner_flag():
        return {'is_owner': is_owner_user(session.get('username'))}

    with app.app_context():
        db.create_all()

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
