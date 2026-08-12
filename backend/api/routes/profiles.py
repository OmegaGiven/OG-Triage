"""Lists registered company profiles -- backs the frontend's profile switcher."""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import ProfileOut
from profiles import PROFILES

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileOut])
def list_profiles() -> list[ProfileOut]:
    return [
        ProfileOut(key=profile.key, display_name=profile.display_name)
        for profile in PROFILES.values()
    ]
