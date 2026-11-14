"""Account management for admins: list users, create one with a role, change a role.

Public sign-up (pages.register) only makes viewers; this is the one place that grants admin.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.auth import admin_required, create_user
from app.forms import ROLE_LABELS, UserCreateForm
from app.models import ROLES, User, db

bp = Blueprint("users", __name__, url_prefix="/admin/users")


@bp.get("/")
@admin_required
def list_users():
    users = db.session.scalars(select(User).order_by(User.username)).all()
    return render_template(
        "users/list.html", users=users, roles=ROLE_LABELS, active="users"
    )


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def new_user():
    form = UserCreateForm()
    if form.validate_on_submit():
        try:
            create_user(form.username.data, form.password.data, form.role.data)
        except ValueError:
            form.username.errors.append("Потребителското име е заето.")
        else:
            flash("Потребителят е добавен.", "success")
            return redirect(url_for("users.list_users"))
    return render_template("users/form.html", form=form, active="users")


@bp.post("/<int:user_id>/role")
@admin_required
def set_role(user_id):
    user = db.get_or_404(User, user_id)
    role = request.form.get("role")
    if role not in ROLES:
        abort(400)
    if user.id == current_user.id:
        # Also guarantees there is always at least one admin.
        flash("Не можете да променяте собствената си роля.", "danger")
    else:
        user.role = role
        db.session.commit()
        flash(
            f"Ролята на „{user.username}“ е променена на „{ROLE_LABELS[role]}“.",
            "success",
        )
    return redirect(url_for("users.list_users"))
