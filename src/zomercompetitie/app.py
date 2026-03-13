from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from zomercompetitie.db import Base, SessionLocal, engine
from zomercompetitie.models import Attendance, Evening, Match, MatchPhase, Player
from zomercompetitie.services import (
    create_groups_for_evening,
    create_knockout,
    ensure_evening,
    group_rankings_for_evening,
    maybe_progress_knockout,
    overall_standings,
    save_match_result,
    upsert_player_stats,
)

app = FastAPI(title="Zomercompetitie")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    standings = overall_standings(db)
    latest_id = evenings[0].id if evenings else None
    latest_matches = (
        db.scalars(select(Match).where(Match.evening_id == latest_id).order_by(Match.phase, Match.bracket_order)).all()
        if latest_id
        else []
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "evenings": evenings,
            "standings": standings,
            "latest_matches": latest_matches,
        },
    )


@app.post("/players")
def create_player(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Player(name=name.strip()))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/players/{player_id}/toggle")
def toggle_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    player.active = not player.active
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin")
def admin(request: Request, db: Session = Depends(get_db)):
    players = db.scalars(select(Player).order_by(Player.name)).all()
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    return templates.TemplateResponse("admin.html", {"request": request, "players": players, "evenings": evenings})


@app.post("/evenings")
def create_evening(event_date: str = Form(...), db: Session = Depends(get_db)):
    evening = Evening(event_date=date.fromisoformat(event_date))
    db.add(evening)
    db.commit()
    return RedirectResponse(f"/evenings/{evening.id}", status_code=303)


@app.get("/evenings/{evening_id}")
def evening_detail(request: Request, evening_id: int, db: Session = Depends(get_db)):
    evening = db.execute(
        select(Evening)
        .options(joinedload(Evening.attendances).joinedload(Attendance.player), joinedload(Evening.matches))
        .where(Evening.id == evening_id)
    ).unique().scalar_one_or_none()
    if not evening:
        raise HTTPException(404)

    players = db.scalars(select(Player).where(Player.active.is_(True)).order_by(Player.name)).all()
    group_rows = group_rankings_for_evening(db, evening.id) if evening.groups else []
    return templates.TemplateResponse(
        "evening_detail.html",
        {
            "request": request,
            "evening": evening,
            "players": players,
            "group_rows": group_rows,
        },
    )


@app.post("/evenings/{evening_id}/attendance")
def update_attendance(evening_id: int, player_id: int = Form(...), present: bool = Form(False), db: Session = Depends(get_db)):
    ensure_evening(db, evening_id)
    row = db.scalars(select(Attendance).where(Attendance.evening_id == evening_id, Attendance.player_id == player_id)).first()
    if not row:
        row = Attendance(evening_id=evening_id, player_id=player_id)
        db.add(row)
    row.present = present
    db.commit()
    return RedirectResponse(f"/evenings/{evening_id}", status_code=303)


@app.post("/evenings/{evening_id}/groups")
def generate_groups(evening_id: int, db: Session = Depends(get_db)):
    evening = ensure_evening(db, evening_id)
    create_groups_for_evening(db, evening)
    db.commit()
    return RedirectResponse(f"/evenings/{evening_id}", status_code=303)


@app.post("/evenings/{evening_id}/knockout")
def generate_knockout(evening_id: int, db: Session = Depends(get_db)):
    evening = ensure_evening(db, evening_id)
    create_knockout(db, evening)
    db.commit()
    return RedirectResponse(f"/evenings/{evening_id}", status_code=303)


@app.post("/matches/{match_id}/result")
def submit_result(
    match_id: int,
    legs1: int = Form(...),
    legs2: int = Form(...),
    high1: int = Form(0),
    high2: int = Form(0),
    one80_1: int = Form(0),
    one80_2: int = Form(0),
    fast1: int = Form(0),
    fast2: int = Form(0),
    db: Session = Depends(get_db),
):
    match = save_match_result(db, match_id, legs1, legs2)
    upsert_player_stats(db, match.evening_id, match.player1_id, high1, one80_1, fast1)
    upsert_player_stats(db, match.evening_id, match.player2_id, high2, one80_2, fast2)
    evening = db.get(Evening, match.evening_id)
    maybe_progress_knockout(db, evening)
    db.commit()
    return RedirectResponse(f"/evenings/{match.evening_id}", status_code=303)


@app.get("/pwa/manifest.webmanifest")
def manifest():
    return {
        "name": "Zomercompetitie",
        "short_name": "Zomercomp",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0d1b2a",
        "theme_color": "#0d1b2a",
        "icons": [],
    }
