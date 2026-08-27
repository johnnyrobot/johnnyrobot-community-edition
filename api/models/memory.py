"""
Memory-related Pydantic models for request/response validation.
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class MemoryItem(BaseModel):
    """Individual memory item."""
    id: str
    memory: str
    user_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: dict = {}


class MemoryListResponse(BaseModel):
    """List of memories response."""
    memories: List[MemoryItem]
    total: int


class MemoryDeleteResponse(BaseModel):
    """Memory deletion response."""
    message: str
    memory_id: Optional[str] = None
    deleted_count: Optional[int] = None
