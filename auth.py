import hashlib
import secrets

from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session, Device
from config import settings


def generate_api_key() -> str:
    """Generates a new random API key. Shown to the user ONCE at registration time."""
    return secrets.token_hex(32)


def hash_api_key(raw_key: str) -> str:
    """Never store raw API keys — same principle as Django's password hashing."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def verify_device(
    x_device_key: str = Header(..., description="The device's API key"),
    session: AsyncSession = Depends(get_session),
) -> Device:
    """
    FastAPI dependency: checks the X-Device-Key header against registered
    devices. Raises 401 if the key doesn't match anything on file.
    Use with Depends(verify_device) on any route that needs authentication.
    """
    hashed = hash_api_key(x_device_key)
    result = await session.execute(select(Device).where(Device.hashed_api_key == hashed))
    device = result.scalars().first()

    if device is None:
        raise HTTPException(status_code=401, detail="Invalid or missing device API key")

    return device


async def verify_admin(x_admin_key: str = Header(..., description="Admin API key")) -> None:
    """
    FastAPI dependency for admin-only routes, like device registration.
    Compares against the ADMIN_API_KEY set in the environment/.env file.
    """
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
