"""Data models for the attendance system"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ProcessingStatus(BaseModel):
    """Processing status model"""
    status: str
    message: str
    progress: int
    timestamp: datetime

class FileInfo(BaseModel):
    """File information model"""
    filename: str
    size: int
    uploaded_at: datetime
    file_type: Optional[str] = None

class ProcessingResponse(BaseModel):
    """Processing response model"""
    status: str
    message: str
    file_url: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    logs: Optional[List[str]] = None

class WebSocketMessage(BaseModel):
    """WebSocket message model"""
    type: str  # 'log', 'progress', 'complete', 'error'
    message: str
    progress: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None