from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_user, logout_user

from app.auth import authenticate, limiter

bp = Blueprint("pages", __name__, template_folder="templates")


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def login():
    if request.method == "POST":
        user = authenticate(
            request.form.get("username", "")[:64],
            request.form.get("password", "")[:128],
        )
        if user:
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(url_for("pages.index"))
        # Never say which part was wrong.
        flash("Грешно потребителско име или парола.", "danger")
        return render_template("login.html"), 401
    return render_template("login.html")


# Not behind @admin_required on purpose: a viewer must be able to log out.
@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("pages.index"))
