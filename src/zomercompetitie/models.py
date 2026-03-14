from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zomercompetitie.db import Base


class MatchPhase(str, Enum):
    GROUP = "GROUP"
    QUARTER = "QUARTER"
    SEMI = "SEMI"
    FINAL = "FINAL"


class EveningStatus(str, Enum):
    DRAFT = "DRAFT"
    GROUP_ACTIVE = "GROUP_ACTIVE"
    KNOCKOUT_ACTIVE = "KNOCKOUT_ACTIVE"
    CLOSED = "CLOSED"


class SeasonStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Evening(Base):
    __tablename__ = "evenings"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, unique=True)
    status: Mapped[EveningStatus] = mapped_column(SAEnum(EveningStatus), default=EveningStatus.DRAFT)
    board_count: Mapped[int] = mapped_column(Integer, default=2)

    attendances: Mapped[list[Attendance]] = relationship(back_populates="evening", cascade="all, delete-orphan")
    groups: Mapped[list[Group]] = relationship(back_populates="evening", cascade="all, delete-orphan")
    matches: Mapped[list[Match]] = relationship(back_populates="evening", cascade="all, delete-orphan")
    season_links: Mapped[list[SeasonEvening]] = relationship(back_populates="evening", cascade="all, delete-orphan")


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[SeasonStatus] = mapped_column(SAEnum(SeasonStatus), default=SeasonStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    evening_links: Mapped[list[SeasonEvening]] = relationship(back_populates="season", cascade="all, delete-orphan")


class SeasonEvening(Base):
    __tablename__ = "season_evenings"
    __table_args__ = (UniqueConstraint("season_id", "evening_id", name="uq_season_evening"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    evening_id: Mapped[int] = mapped_column(ForeignKey("evenings.id"), unique=True)

    season: Mapped[Season] = relationship(back_populates="evening_links")
    evening: Mapped[Evening] = relationship(back_populates="season_links")


class Attendance(Base):
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("evening_id", "player_id", name="uq_evening_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evening_id: Mapped[int] = mapped_column(ForeignKey("evenings.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    present: Mapped[bool] = mapped_column(Boolean, default=True)

    evening: Mapped[Evening] = relationship(back_populates="attendances")
    player: Mapped[Player] = relationship()


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    evening_id: Mapped[int] = mapped_column(ForeignKey("evenings.id"))
    name: Mapped[str] = mapped_column(String(20))

    evening: Mapped[Evening] = relationship(back_populates="groups")
    assignments: Mapped[list[GroupAssignment]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupAssignment(Base):
    __tablename__ = "group_assignments"
    __table_args__ = (UniqueConstraint("group_id", "player_id", name="uq_group_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))

    group: Mapped[Group] = relationship(back_populates="assignments")
    player: Mapped[Player] = relationship()


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    evening_id: Mapped[int] = mapped_column(ForeignKey("evenings.id"))
    phase: Mapped[MatchPhase] = mapped_column(SAEnum(MatchPhase))
    bracket_order: Mapped[int] = mapped_column(Integer, default=0)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    player1_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player2_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    legs_player1: Mapped[int] = mapped_column(Integer, default=0)
    legs_player2: Mapped[int] = mapped_column(Integer, default=0)
    board_number: Mapped[int] = mapped_column(Integer, default=1)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)

    evening: Mapped[Evening] = relationship(back_populates="matches")
    player1: Mapped[Player] = relationship(foreign_keys=[player1_id])
    player2: Mapped[Player] = relationship(foreign_keys=[player2_id])
    stats: Mapped[list[MatchPlayerStat]] = relationship(back_populates="match", cascade="all, delete-orphan")


class MatchPlayerStat(Base):
    __tablename__ = "match_player_stats"
    __table_args__ = (UniqueConstraint("match_id", "player_id", name="uq_match_player_stat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    evening_id: Mapped[int] = mapped_column(ForeignKey("evenings.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    high_finishes_100: Mapped[int] = mapped_column(Integer, default=0)
    high_finishes_100_values: Mapped[str] = mapped_column(String(500), default="")
    one_eighty: Mapped[int] = mapped_column(Integer, default=0)
    fast_legs_15: Mapped[int] = mapped_column(Integer, default=0)
    fast_legs_15_values: Mapped[str] = mapped_column(String(500), default="")

    match: Mapped[Match] = relationship(back_populates="stats")
    player: Mapped[Player] = relationship()
