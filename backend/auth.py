"""
Firebase Authentication for CMBAgent Backend.

Supports both Firebase Auth (production) and local development mode.
"""

import os
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Check if we're in local development mode
LOCAL_DEV_MODE = os.environ.get("CMBAGENT_LOCAL_DEV", "true").lower() == "true"

# Firebase Admin SDK (only import if not in local dev mode)
firebase_app = None
if not LOCAL_DEV_MODE:
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth, credentials

        # Initialize Firebase Admin
        if not firebase_admin._apps:
            cred_path = os.environ.get("FIREBASE_CREDENTIALS", "firebase-credentials.json")
            if os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
                firebase_app = firebase_admin.initialize_app(cred)
                logger.info("Firebase Admin initialized with credentials file")
            else:
                # Use default credentials (for Cloud Run)
                try:
                    cred = credentials.ApplicationDefault()
                    firebase_app = firebase_admin.initialize_app(cred)
                    logger.info("Firebase Admin initialized with default credentials")
                except Exception as e:
                    logger.warning(f"Failed to initialize Firebase: {e}")
                    logger.warning("Falling back to local development mode")
                    LOCAL_DEV_MODE = True
    except ImportError:
        logger.warning("firebase-admin not installed, using local development mode")
        LOCAL_DEV_MODE = True

security = HTTPBearer(auto_error=False)


class User(BaseModel):
    """Authenticated user information."""
    uid: str
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    is_local_dev: bool = False


class LocalDevUser(User):
    """User for local development."""

    @classmethod
    def create(cls) -> "LocalDevUser":
        return cls(
            uid="local-dev-user",
            email="dev@localhost",
            name="Local Developer",
            is_local_dev=True,
        )


async def verify_firebase_token(token: str) -> Optional[User]:
    """
    Verify a Firebase ID token and return user info.

    Args:
        token: Firebase ID token

    Returns:
        User object if valid, None otherwise
    """
    if LOCAL_DEV_MODE:
        return None

    try:
        from firebase_admin import auth as firebase_auth

        decoded = firebase_auth.verify_id_token(token)
        return User(
            uid=decoded["uid"],
            email=decoded.get("email"),
            name=decoded.get("name"),
            picture=decoded.get("picture"),
            is_local_dev=False,
        )
    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    """
    Get the current authenticated user.

    In local development mode, returns a mock user.
    In production, verifies the Firebase token.

    Args:
        credentials: HTTP Authorization header

    Returns:
        User object

    Raises:
        HTTPException: If authentication fails (production mode only)
    """
    # Local development mode - return mock user
    if LOCAL_DEV_MODE:
        return LocalDevUser.create()

    # Production mode - verify token
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await verify_firebase_token(credentials.credentials)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[User]:
    """
    Get the current user if authenticated, None otherwise.

    Useful for endpoints that work with or without authentication.
    """
    if LOCAL_DEV_MODE:
        return LocalDevUser.create()

    if not credentials:
        return None

    return await verify_firebase_token(credentials.credentials)


async def verify_ws_token(token: Optional[str]) -> Optional[User]:
    """
    Verify a token for WebSocket connections.

    Args:
        token: Token from query parameter

    Returns:
        User object if valid, mock user in dev mode, None otherwise
    """
    if LOCAL_DEV_MODE:
        return LocalDevUser.create()

    if not token:
        return None

    return await verify_firebase_token(token)


def require_auth_ws(token: Optional[str] = Query(None)) -> User:
    """
    Dependency for WebSocket endpoints requiring authentication.

    Usage:
        @app.websocket("/ws/{task_id}")
        async def websocket_endpoint(
            websocket: WebSocket,
            task_id: str,
            user: User = Depends(require_auth_ws)
        ):
            ...
    """
    import asyncio

    loop = asyncio.get_event_loop()
    user = loop.run_until_complete(verify_ws_token(token))

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    return user


# Helper functions for user management

async def get_or_create_user_profile(user: User) -> dict:
    """
    Get or create a user profile in Firestore.

    Returns the user profile data.
    """
    if LOCAL_DEV_MODE:
        return {
            "uid": user.uid,
            "email": user.email,
            "name": user.name,
            "plan": "free",
            "created_at": "2024-01-01T00:00:00Z",
        }

    # In production, this would interact with Firestore
    # For now, return basic profile
    return {
        "uid": user.uid,
        "email": user.email,
        "name": user.name,
        "plan": "free",
    }


def is_local_dev() -> bool:
    """Check if running in local development mode."""
    return LOCAL_DEV_MODE
