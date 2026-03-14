from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(os.getenv("ZOMERCOMP_DB_PATH", "data/zomercompetitie.db")).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def run_sqlite_migrations() -> None:
    with engine.begin() as conn:
        table_names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}

        if "match_player_stats" in table_names:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(match_player_stats)"))}
            if "high_finishes_100_values" not in columns:
                conn.execute(text("ALTER TABLE match_player_stats ADD COLUMN high_finishes_100_values VARCHAR(500) DEFAULT ''"))
            if "fast_legs_15_values" not in columns:
                conn.execute(text("ALTER TABLE match_player_stats ADD COLUMN fast_legs_15_values VARCHAR(500) DEFAULT ''"))
