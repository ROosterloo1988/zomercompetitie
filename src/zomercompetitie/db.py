from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(os.getenv("ZOMERCOMP_DB_PATH", "data/zomercompetitie.db")).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", future=True)

# --- KING UPGRADE: WAL MODE & CONCURRENCY ---
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000") # Wacht max 5 seconden bij drukte ipv crashen
    cursor.close()
# -------------------------------------------

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
