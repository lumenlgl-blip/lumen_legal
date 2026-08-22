from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, nullable=False, default=1)  # Temporal
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    role = Column(String(20), default="abogado")  # admin, abogado, asistente
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)