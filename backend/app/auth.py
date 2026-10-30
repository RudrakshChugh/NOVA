"""Session tokens and role checks. Login itself lives in routers/auth.py."""

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


PENDING = "pending"


def require_role(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role == PENDING:
            # A distinct code so the interface can show "waiting for approval" rather than a flat
            # permission error — the account is valid, it just has no role yet.
            raise HTTPException(403, "account pending approval")
        if user.role not in roles:
            raise HTTPException(403, f"requires role {' or '.join(roles)}")
        return user

    return dep


def require_active(user: User = Depends(current_user)) -> User:
    """Any granted role. Used by the read-only endpoints that every signed-in member may see."""
    if user.role == PENDING:
        raise HTTPException(403, "account pending approval")
    return user
