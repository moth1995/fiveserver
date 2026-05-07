"""Registration blueprint: user sign-up and password-recovery flow."""

from __future__ import annotations

from typing import Any

import captcha as _captcha
from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    send_from_directory,
)

from crypto import blowfish_encrypt
from config import is_banned
from db import (
    get_db,
    find_user_by_username,
    find_user_by_nonce,
    create_user,
    update_user,
    create_profiles_for_user,
    record_user_registration,
)

register_bp = Blueprint("register", __name__)


def _captcha_template_vars() -> dict[str, str]:
    provider_name: str = current_app.config.get("CAPTCHA_PROVIDER", "")
    provider = _captcha.get_provider(provider_name)
    if provider is None:
        return {
            "captcha_script_url": "",
            "captcha_widget_class": "",
            "captcha_site_key": "",
        }
    return {
        "captcha_script_url": provider.script_url,
        "captcha_widget_class": provider.widget_class,
        "captcha_site_key": current_app.config.get("CAPTCHA_SITE_KEY", ""),
    }


@register_bp.route("/")
def form() -> str:
    return render_template(
        "register/form.html",
        serial="",
        username="",
        nonce="",
        **_captcha_template_vars(),
    )


@register_bp.route("/md5.js")
def md5_js():  # type: ignore[return]
    static_folder: str = current_app.static_folder or ""
    return send_from_directory(static_folder, "md5.js")


@register_bp.route("/modifyUser/<nonce>")
def modify_user(nonce: str) -> str:
    conn = get_db()
    user: dict[str, Any] | None = find_user_by_nonce(conn, nonce)
    if user is None:
        abort(404)
    return render_template(
        "register/form.html",
        serial=user["serial"],
        username=user["username"],
        nonce=nonce,
        **_captcha_template_vars(),
    )


@register_bp.route("/register", methods=["POST"])
def register():  # type: ignore[return]
    remote_ip: str = request.remote_addr or "0.0.0.0"
    banned_list = current_app.config.get("BANNED_LIST", [])
    if is_banned(remote_ip, banned_list):
        abort(403)

    provider_name: str = current_app.config.get("CAPTCHA_PROVIDER", "")
    secret_key: str = current_app.config.get("CAPTCHA_SECRET_KEY", "")
    if provider_name and secret_key:
        provider = _captcha.get_provider(provider_name)
        token: str = request.form.get(provider.token_field, "") if provider else ""
        if not _captcha.verify(token, secret_key, provider_name):
            return (
                render_template(
                    "register/result.html",
                    message="ERROR: CAPTCHA verification failed",
                    success=False,
                ),
                400,
            )

    serial: str = request.form.get("serial", "")
    username: str = request.form.get("user", "")
    hex_hash: str = request.form.get("hash", "")
    nonce: str = request.form.get("nonce", "")

    # Encrypt the client-supplied MD5 hash before storing
    encrypted_hash: str = blowfish_encrypt(hex_hash)

    conn = get_db()

    if not nonce:
        # New registration
        existing = find_user_by_username(conn, username)
        if existing is not None:
            return (
                render_template(
                    "register/result.html",
                    message="ERROR: username is already taken",
                    success=False,
                ),
                409,
            )
        new_id: int = create_user(conn, username, serial, encrypted_hash)
        create_profiles_for_user(conn, new_id)
        record_user_registration(conn, new_id)
        return render_template(
            "register/result.html",
            message="Registration complete",
            success=True,
        )
    else:
        # Password / serial modification via nonce
        user: dict[str, Any] | None = find_user_by_nonce(conn, nonce)
        if user is None:
            return (
                render_template(
                    "register/result.html",
                    message="ERROR: invalid or expired recovery link",
                    success=False,
                ),
                404,
            )
        update_user(conn, user["id"], username, serial, encrypted_hash)
        return render_template(
            "register/result.html",
            message="Account updated successfully",
            success=True,
        )
