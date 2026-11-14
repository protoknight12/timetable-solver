from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_user, logout_user

from app.auth import authenticate, create_user, limiter
from app.forms import RegisterForm

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


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def register():
    """Public sign-up. Always a viewer: only an admin can grant the admin role."""
    if current_user.is_authenticated:
        return redirect(url_for("pages.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        try:
            create_user(form.username.data, form.password.data, "viewer")
        except ValueError:  # lost a race with another sign-up of the same name
            form.username.errors.append("Потребителското име е заето.")
        else:
            flash("Регистрацията е успешна. Влезте с новия си профил.", "success")
            return redirect(url_for("pages.login"))
    return render_template("register.html", form=form), 400 if form.errors else 200


# Not behind @admin_required on purpose: a viewer must be able to log out.
@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("pages.index"))
