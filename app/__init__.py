from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        # Placeholder until FE-02 adds the real layout.
        return "<h1>SATurn</h1><p>Приложението работи.</p>"

    return app
