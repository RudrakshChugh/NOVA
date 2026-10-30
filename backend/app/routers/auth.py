"""Institutional sign-in (plan §4).

Google Workspace is an OIDC provider, so the authorisation-code flow below works unchanged against
`https://accounts.google.com`. Three things are enforced here rather than trusted from the request:

  * `hd` on the authorization URL only filters Google's own account picker, so the domain is
    re-checked on the claims that come back;
  * `email_verified` must be true, because an unverified address proves nothing;
  * the CSRF `state` is a signed, time-limited token this service issued, not an opaque value.

A first-time sign-in creates the user with role `pending`, which satisfies no role check anywhere.
Only an address on COORDINATOR_EMAILS is granted a role automatically; everyone else is granted one
by a coordinator in the app.
"""

import secrets
from functools import lru_cache
from urllib.parse import urlencode

from authlib.integrations.httpx_client import OAuth2Client
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, issue_token, require_role
from ..config import settings
from ..db import get_db
from ..models import User
from ..schemas import DevLoginIn, RoleIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

ROLES = ("pending", "reviewer", "coordinator")
STATE_MAX_AGE = 600  # ten minutes between leaving for Google and coming back
_state_serializer = URLSafeTimedSerializer(settings.session_secret, salt="oauth-state")


def _oidc_configured() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret)


def _oidc_client() -> OAuth2Client:
    return OAuth2Client(
        settings.oidc_client_id, settings.oidc_client_secret,
        scope="openid email profile", redirect_uri=settings.oidc_redirect_uri,
    )


@lru_cache(maxsize=4)
def _discovery(issuer: str) -> dict:
    """Cached: the document is static, and re-fetching it on every sign-in adds a round trip to
    Google in front of the one the user is waiting for."""
    import httpx

    return httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10).json()


def _back_to_app(**params: str) -> RedirectResponse:
    """Hand the browser back to the single-page app.

    The session token travels in the URL *fragment*: fragments are not sent to servers, so it stays
    out of access logs, proxy logs and the Referer header, unlike a query parameter. The app reads
    it once and clears it from the address bar.
    """
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/#{urlencode(params)}", status_code=303)


def _email_allowed(email: str, hd: str | None) -> bool:
    email = email.lower()
    if email in settings.email_exceptions:
        return True
    if not settings.allowed_domain:
        return True
    domain = settings.allowed_domain.lower()
    # Both must agree: the Workspace domain claim and the address itself.
    return email.endswith(f"@{domain}") and (hd is None or hd.lower() == domain)


def _provision(db: Session, info: dict) -> User:
    """Find or create the user. A known user keeps the role they were granted."""
    email = info["email"].lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user
    user = User(
        email=email,
        name=info.get("name") or email.split("@")[0],
        role="coordinator" if email in settings.coordinators else "pending",
    )
    db.add(user)
    db.commit()
    return user


# --- sign in ---


@router.get("/login")
def login_redirect():
    """Where the "Sign in" button points. Starts the authorisation-code flow."""
    if not _oidc_configured():
        raise HTTPException(400, "no identity provider is configured")
    # The state is signed here and handed to Authlib, rather than letting it generate one and
    # appending ours: the authorization URL must carry exactly one `state`, and a second copy makes
    # which value comes back ambiguous. Signing it lets the callback verify this service issued the
    # request without keeping any server-side session.
    state = _state_serializer.dumps(secrets.token_urlsafe(16))
    url, _ = _oidc_client().create_authorization_url(
        _discovery(settings.oidc_issuer)["authorization_endpoint"],
        state=state,
        **({"hd": settings.allowed_domain} if settings.allowed_domain else {}),
    )
    return RedirectResponse(url, status_code=303)


@router.post("/login", response_model=TokenOut | None)
def login(body: DevLoginIn | None = None, db: Session = Depends(get_db)):
    """Development sign-in by email, for when no identity provider is configured."""
    if _oidc_configured():
        return login_redirect()
    if not settings.dev_login or body is None:
        raise HTTPException(400, "OIDC not configured and dev login disabled")
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise HTTPException(404, "unknown user")
    return TokenOut(token=issue_token(user), user=UserOut.model_validate(user, from_attributes=True))


@router.get("/callback")
def callback(code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """Google sends the browser here. Every outcome is a redirect back into the app, because the
    person is looking at a browser window, not reading a JSON response."""
    if not _oidc_configured():
        raise HTTPException(400, "OIDC not configured")
    if error:
        return _back_to_app(error="cancelled")
    try:
        _state_serializer.loads(state, max_age=STATE_MAX_AGE)
    except BadSignature:
        return _back_to_app(error="state")

    disc = _discovery(settings.oidc_issuer)
    client = _oidc_client()
    try:
        client.fetch_token(disc["token_endpoint"], code=code)
        info = client.get(disc["userinfo_endpoint"]).json()
    except Exception:
        return _back_to_app(error="exchange")

    if not info.get("email") or not info.get("email_verified"):
        return _back_to_app(error="unverified")
    if not _email_allowed(info["email"], info.get("hd")):
        return _back_to_app(error="domain")

    user = _provision(db, info)
    return _back_to_app(token=issue_token(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def users(db: Session = Depends(get_db)):
    """Seeded users, for the development sign-in picker only."""
    if not settings.dev_login:
        raise HTTPException(404)
    return db.scalars(select(User).order_by(User.role, User.name)).all()


# --- role management ---


@router.get("/people", response_model=list[UserOut], dependencies=[Depends(require_role("coordinator"))])
def people(db: Session = Depends(get_db)):
    """Everyone who has signed in, so a coordinator can see who is waiting."""
    return db.scalars(select(User).order_by(User.role, User.email)).all()


@router.post("/people/{user_id}/role", response_model=UserOut)
def set_role(user_id: str, body: RoleIn, db: Session = Depends(get_db),
             actor: User = Depends(require_role("coordinator"))):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "unknown user")
    # Changing your own role is refused rather than guarded by a count: a coordinator who demotes
    # themselves locks the deployment out of its only privileged screen, with no way back in.
    if target.user_id == actor.user_id:
        raise HTTPException(400, "you cannot change your own role")
    target.role = body.role
    db.commit()
    return target
