"""Accounts: password hashing, JWT issuing, and request authentication.

Auth is deliberately OPTIONAL on the clipping endpoints for now. Requiring a
login today would break the current flow for no gain, since nothing is metered
yet -- so `current_user_optional` returns None for anonymous callers and the job
is simply saved without an owner. When credits start being enforced, the
endpoints switch to `current_user_required` and anonymous jobs stop.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User

# Must be set in production: a rotating default would silently invalidate every
# session on restart, and a hardcoded one would be forgeable by anyone with the
# source. The dev fallback is fine locally and loudly wrong to ship.
SECRET_KEY = os.environ.get("CLIPPER_SECRET_KEY", "dev-only-insecure-change-me")
ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 30

MIN_PASSWORD_LENGTH = 8

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    # bcrypt silently truncates beyond 72 bytes; rejecting is safer than
    # accepting a password whose tail never mattered.
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password is too long (max 72 bytes).")
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def create_token(user_id: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
    return jwt.encode({"sub": user_id, "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def _user_from_request(request: Request, session: Session):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(header[7:], SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return session.get(User, user_id)


def current_user_optional(request: Request, session: Session = Depends(get_session)):
    """None when signed out -- never raises, so anonymous use keeps working."""
    return _user_from_request(request, session)


def current_user_required(request: Request, session: Session = Depends(get_session)) -> User:
    user = _user_from_request(request, session)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user
