"""A browser proof independent of Django's intentionally rotated login session."""
import secrets
from django.conf import settings

COOKIE_NAME = 'b16_recipient_browser'
COOKIE_SALT = 'b16.recipient.browser.v1'


def browser_nonce(request, create=False):
    nonce = request.get_signed_cookie(COOKIE_NAME, default=None, salt=COOKIE_SALT, max_age=settings.INVITATION_TTL_SECONDS)
    if not nonce:
        # Preserve in-progress pre-upgrade challenges that still have their session.
        nonce = request.session.get('invitation_browser_nonce')
    if not nonce and create:
        nonce = secrets.token_urlsafe(32)
    return nonce


def set_browser_cookie(response, nonce):
    if nonce:
        response.set_signed_cookie(COOKIE_NAME, nonce, salt=COOKIE_SALT, max_age=settings.INVITATION_TTL_SECONDS, httponly=True, secure=settings.SESSION_COOKIE_SECURE, samesite='Lax', path='/api')
    return response
