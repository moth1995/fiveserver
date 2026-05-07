"""CAPTCHA provider abstraction.

Supported providers (set via CAPTCHA_PROVIDER env var):
  google      — Google reCAPTCHA v2
  cloudflare  — Cloudflare Turnstile
  hcaptcha    — hCaptcha

Leave CAPTCHA_PROVIDER unset (or empty) to disable CAPTCHA entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests as _requests

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Provider:
    script_url: str
    widget_class: str  # CSS class on the container div
    token_field: str  # POST field name submitted by the widget
    verify_url: str


_PROVIDERS: dict[str, _Provider] = {
    "google": _Provider(
        script_url="https://www.google.com/recaptcha/api.js",
        widget_class="g-recaptcha",
        token_field="g-recaptcha-response",
        verify_url="https://www.google.com/recaptcha/api/siteverify",
    ),
    "cloudflare": _Provider(
        script_url="https://challenges.cloudflare.com/turnstile/v0/api.js",
        widget_class="cf-turnstile",
        token_field="cf-turnstile-response",
        verify_url="https://challenges.cloudflare.com/turnstile/v0/siteverify",
    ),
    "hcaptcha": _Provider(
        script_url="https://js.hcaptcha.com/1/api.js",
        widget_class="h-captcha",
        token_field="h-captcha-response",
        verify_url="https://hcaptcha.com/siteverify",
    ),
}


def get_provider(name: str) -> _Provider | None:
    """Return the provider descriptor for *name*, or None if unknown/empty."""
    return _PROVIDERS.get(name.lower()) if name else None


def template_vars(provider_name: str, site_key: str) -> dict[str, str]:
    """Return template context dict for rendering the CAPTCHA widget."""
    provider = get_provider(provider_name)
    if provider is None or not site_key:
        return {
            "captcha_script_url": "",
            "captcha_widget_class": "",
            "captcha_site_key": "",
        }
    return {
        "captcha_script_url": provider.script_url,
        "captcha_widget_class": provider.widget_class,
        "captcha_site_key": site_key,
    }


# ---------------------------------------------------------------------------
# Server-side verification
# ---------------------------------------------------------------------------


def verify(token: str, secret: str, provider_name: str) -> bool:
    """Verify *token* against the named provider. Returns False on any error."""
    provider = get_provider(provider_name)
    if provider is None:
        return False
    try:
        resp = _requests.post(
            provider.verify_url,
            data={"secret": secret, "response": token},
            timeout=5,
        )
        return bool(resp.json().get("success"))
    except Exception:
        return False
