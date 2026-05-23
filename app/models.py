from __future__ import annotations

from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class Reference(BaseModel):
    type: str       # "image" | "video" | "audio"
    cos_url: str
    filename: str


class TaskParams(BaseModel):
    duration: int = 5
    ratio: str = "16:9"
    model_version: str = "seedance2.0fast"


class TaskCreate(BaseModel):
    prompt: str
    duration: int = 5
    ratio: str = "16:9"
    model_version: str = "seedance2.0fast"
    references: List[Reference] = []


class TaskUpdate(BaseModel):
    prompt: Optional[str] = None
    duration: Optional[int] = None
    ratio: Optional[str] = None
    model_version: Optional[str] = None
    references: Optional[List[Reference]] = None


class ReorderRequest(BaseModel):
    position: int


class TaskResponse(BaseModel):
    id: int
    type: str
    status: str
    prompt: str
    params: TaskParams
    references: List[Reference]
    submit_id: Optional[str] = None
    result_url: Optional[str] = None
    gen_status: Optional[str] = None
    error_message: Optional[str] = None
    position: int
    session_id: int
    created_at: str
    updated_at: str


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str


class QueueStatus(BaseModel):
    running: Optional[TaskResponse] = None
    pending_count: int
    done_count: int
    failed_count: int
    paused: bool


class CreditResponse(BaseModel):
    total_credit: int


class HealthResponse(BaseModel):
    ok: bool
    cli_installed: bool
    login_status: str
