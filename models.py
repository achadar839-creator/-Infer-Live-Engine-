from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from database import Base

class Stream(Base):
    __tablename__ = "streams"

    id = Column(Integer, primary_key=True, index=True)
    video_path = Column(String(1000), nullable=False)
    custom_text = Column(Text, default="")
    status = Column(String(30), default="stopped")
    auto_restart = Column(Boolean, default=True)
    encrypted_stream_key = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
