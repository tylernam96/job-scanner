"""Load and validate the master profile and config files."""
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
# Your real profile lives in profile/ (git-ignored); without it the fictional example is used.
PROFILE_DIR = Path(os.environ.get("PROFILE_DIR") or (
    ROOT / "profile" if (ROOT / "profile/master_profile.json").exists() else ROOT / "profile/example"))


class Bullet(BaseModel):
    id: str
    text: str
    skills: list[str]
    metrics: list[str]


class Experience(BaseModel):
    id: str
    company: str
    title: str
    location: str
    start: str
    end: str | None
    bullets: list[Bullet]


class Education(BaseModel):
    school: str
    degree: str
    location: str
    start: str
    end: str


class Skills(BaseModel):
    tools: list[str]
    methods: list[str]
    practices: list[str]


class MasterProfile(BaseModel):
    status: Literal["draft", "approved"]
    name: str
    contact: dict[str, str | None]
    work_setup: dict[str, str | list[str]]
    summary: str
    experience: list[Experience]
    education: list[Education]
    skills: Skills
    domains: list[str]


class Company(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby"]
    slug: str
    hires_globally: Literal["likely", "unverified"]
    blocked: bool = False


def load_profile() -> MasterProfile:
    return MasterProfile.model_validate_json((PROFILE_DIR / "master_profile.json").read_text())


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text())


def load_companies() -> list[Company]:
    return [Company(**c) for c in load_yaml("companies.yaml")["companies"]]
