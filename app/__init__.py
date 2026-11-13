from flask import Flask

from app.routes.pages import bp as pages_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(pages_bp)

    return app
