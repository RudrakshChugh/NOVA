"""Session tokens and platform-standing checks. Login itself lives in routers/auth.py, and what
somebody may do to one subject lives in scoping.py."""

from fastapi import Depends, Header, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="session")
SESSION_MAX_AGE = 12 * 3600


def issue_token(user: User) -> str:
    return _serializer.dumps({"user_id": str(user.user_id)})


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        data = _serializer.loads(authorization.removeprefix("Bearer "), max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "invalid or expired session")
    user = db.get(User, data["user_id"])
    if user is None:
        raise HTTPException(401, "unknown user")
    return user


PENDING, MEMBER, ADMIN = "pending", "member", "admin"


def require_active(user: User = Depends(current_user)) -> User:
    """An admitted account. What it may do to a given subject is decided per subject, in
    `scoping.py`; this only establishes that the account has been admitted at all."""
    if user.role == PENDING:
        # A distinct code so the interface can show "waiting for approval" rather than a flat
        # permission error — the account is valid, it has simply not been admitted yet.
        raise HTTPException(403, "account pending approval")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Platform administration: creating subjects, and admitting or removing people.

    Deliberately separate from being a coordinator. A coordinator runs one subject; an admin
    decides who exists on the platform at all, which is a different and rarer act."""
    if user.role == PENDING:
        raise HTTPException(403, "account pending approval")
    if user.role != ADMIN:
        raise HTTPException(403, "requires a platform administrator")
    return user
