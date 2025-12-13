import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, String, Integer, DateTime, ForeignKey, Enum as SqlEnum
from sqlalchemy.orm import relationship, declarative_base
import enum

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    telegram_id = Column(BigInteger, primary_key=True)
    refresh_token = Column(String, nullable=False)
    access_token = Column(String, nullable=True)
    expires_at = Column(Integer, default=0) # Unix timestamp

class RideStatus(enum.Enum):
    active = "active"
    finished = "finished"
    cancelled = "cancelled"

class ParticipantStatus(enum.Enum):
    going = "going"
    maybe = "maybe"

class Ride(Base):
    __tablename__ = 'rides'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)
    strava_event_id = Column(String, nullable=False, unique=True)
    start_time = Column(DateTime(timezone=True), nullable=False) # UTC
    status = Column(SqlEnum(RideStatus), default=RideStatus.active)
    
    participants = relationship("RideParticipant", back_populates="ride", cascade="all, delete-orphan")

class RideParticipant(Base):
    __tablename__ = 'ride_participants'
    
    ride_id = Column(Integer, ForeignKey('rides.id'), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    username = Column(String, nullable=True)
    status = Column(SqlEnum(ParticipantStatus), default=ParticipantStatus.going)
    
    ride = relationship("Ride", back_populates="participants")

# Setup Engine
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

import asyncio

async def init_db():
    retries = 10
    wait = 2
    for i in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            print("Database initialized successfully.")
            return
        except Exception as e:
            print(f"Database unavailable, retrying in {wait}s... ({e})")
            if i == retries - 1:
                raise e
            await asyncio.sleep(wait)
