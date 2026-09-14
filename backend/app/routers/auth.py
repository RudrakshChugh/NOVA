"""POST /auth/login -> OIDC redirect (or dev login), GET /auth/callback -> session token."""

from authlib.integrations.httpx_client import OAuth2Client
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, issue_token
from ..config import settings
from ..db import get_db
from ..models import User
from ..schemas import DevLoginIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _oidc_configured() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret)


def _oidc_client() -> OAuth2Client:
    return OAuth2Client(settings.oidc_client_id, settings.oidc_client_secret, scope="openid email profile", redirect_uri=settings.oidc_redirect_uri)


def _discovery() -> dict:
    import httpx

    return httpx.get(f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10).json()


@router.post("/login", response_model=TokenOut | None)
def login(body: DevLoginIn | None = None, db: Session = Depends(get_db)):
    if _oidc_configured():
        url, _state = _oidc_client().create_authorization_url(_discovery()["authorization_endpoint"])
        return RedirectResponse(url, status_code=303)
    if not settings.dev_login or body is None:
        raise HTTPException(400, "OIDC not configured and dev login disabled")
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise HTTPException(404, "unknown user")
    return TokenOut(token=issue_token(user), user=UserOut.model_validate(user, from_attributes=True))


@router.get("/callback", response_model=TokenOut)
def callback(code: str, db: Session = Depends(get_db)):
    if not _oidc_configured():
        raise HTTPException(400, "OIDC not configured")
    disc = _discovery()
    client = _oidc_client()
    client.fetch_token(disc["token_endpoint"], code=code)
    info = client.get(disc["userinfo_endpoint"]).json()
    user = db.scalar(select(User).where(User.email == info["email"]))
    if user is None:
        raise HTTPException(403, "user is not provisioned on this platform")
    return TokenOut(token=issue_token(user), user=UserOut.model_validate(user, from_attributes=True))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def users(db: Session = Depends(get_db)):
    """Seeded users, for the dev-login picker."""
    if not settings.dev_login:
        raise HTTPException(404)
    return db.scalars(select(User).order_by(User.role, User.name)).all()
