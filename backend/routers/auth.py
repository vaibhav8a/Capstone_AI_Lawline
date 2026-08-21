"""
auth.py — JWT Authentication + Role-Based Access Control (RBAC)
Roles: admin (index, delete) | researcher (query, export) | viewer (query only)
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Config ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-change-me-in-production")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ── Pwd hashing ──────────────────────────────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

router = APIRouter()

# ── In-memory user store (replace with DB in production) ────────────────────
# Hashes are pre-computed (bcrypt 4.0.1, cost=12) to avoid module-load-time
# failures caused by passlib 1.7.4 / bcrypt >=4.1 incompatibilities.
# Regenerate with: python -c "from passlib.context import CryptContext; ctx=CryptContext(schemes=['bcrypt']); print(ctx.hash('<password>'))"
USERS_DB: dict[str, dict] = {
    "admin": {
        "username": "admin",
        "hashed_password": "$2b$12$N1sGB.1SMmDxRfleVy/SG.EC3.LRXr/brK0tSSvoBJ/3grUIKW3aq",  # admin123
        "role": "admin",
    },
    "researcher": {
        "username": "researcher",
        "hashed_password": "$2b$12$/c4y89TryShSVBFT9Hcqc.6cVd0N66hxSvQ0wcMLj00a8uB6mBBu6",  # research123
        "role": "researcher",
    },
    "viewer": {
        "username": "viewer",
        "hashed_password": "$2b$12$K.VrfV1t/ivNUgXq9ZHtX.Ir9EYS7steRz8XXulWCI3txKZoabXKS",  # view123
        "role": "viewer",
    },
}

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin":      ["query", "index", "export", "delete", "critique"],
    "researcher": ["query", "index", "export", "critique"],
    "viewer":     ["query"],
}

# ── Schemas ──────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    username: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"

# ── Helpers ───────────────────────────────────────────────────────────────────
def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)

def _get_user(username: str) -> Optional[dict]:
    return USERS_DB.get(username)

def _create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = _get_user(username)
    if not user:
        raise credentials_exception
    return user

def require_permission(permission: str):
    """Dependency factory — injects current user and validates permission."""
    async def _check(user: dict = Depends(get_current_user)):
        allowed = ROLE_PERMISSIONS.get(user["role"], [])
        if permission not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' is not allowed to perform '{permission}'"
            )
        return user
    return _check

# ── Routes ────────────────────────────────────────────────────────────────────
@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = _get_user(form_data.username)
    if not user or not _verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_access_token({"sub": user["username"], "role": user["role"]})
    return Token(
        access_token=token,
        token_type="bearer",
        role=user["role"],
        username=user["username"]
    )

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, _admin=Depends(require_permission("delete"))):
    """Only admins can create new users."""
    if req.username in USERS_DB:
        raise HTTPException(status_code=400, detail="Username already exists")
    if req.role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {list(ROLE_PERMISSIONS.keys())}")
    USERS_DB[req.username] = {
        "username": req.username,
        "hashed_password": pwd_ctx.hash(req.password),
        "role": req.role,
    }
    return {"message": f"User '{req.username}' registered with role '{req.role}'"}

@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "username": user["username"],
        "role": user["role"],
        "permissions": ROLE_PERMISSIONS.get(user["role"], [])
    }
