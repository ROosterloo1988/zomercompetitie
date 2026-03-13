from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from zomercompetitie.db import Base, SessionLocal, engine
from zomercompetitie.models import Attendance, Evening, Match, MatchPhase, MatchPlayerStat, Player
from zomercompetitie.services import (
    create_groups_for_evening,
    create_knockout,
    ensure_evening,
    grouped_rankings_for_evening,
    highlights_overview,
    maybe_progress_knockout,
    overall_standings,
    save_match_player_stats,
    save_match_result,
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
    highlights = highlights_overview(db)
    latest = evenings[0] if evenings else None
    latest_matches = (
        db.scalars(select(Match).where(Match.evening_id == latest.id).order_by(Match.phase, Match.bracket_order)).all() if latest else []
    )
    latest_groups = grouped_rankings_for_evening(db, latest.id) if latest and latest.groups else {}
    latest_highlights = highlights_overview(db, latest.id) if latest else []
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "evenings": evenings,
            "standings": standings,
            "latest_matches": latest_matches,
            "latest": latest,
            "latest_groups": latest_groups,
            "highlights": highlights,
            "latest_highlights": latest_highlights,
        },
    )


@app.post("/players")
def create_player(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Player(name=name.strip()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/admin?error=Speler+bestaat+al", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/players/{player_id}/toggle")
def toggle_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    player.active = not player.active
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/players/{player_id}/update")
def update_player(player_id: int, name: str = Form(...), db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    player.name = name.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/admin?error=Naam+is+al+in+gebruik", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin")
def admin(request: Request, error: str | None = None, db: Session = Depends(get_db)):
    players = db.scalars(select(Player).order_by(Player.name)).all()
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    return templates.TemplateResponse("admin.html", {"request": request, "players": players, "evenings": evenings, "error": error})


@app.post("/admin/reset")
def reset_test_data(db: Session = Depends(get_db)):
    db.query(MatchPlayerStat).delete()
    db.query(Match).delete()
    db.query(Attendance).delete()
    db.query(Evening).delete()
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/evenings")
def create_evening(event_date: str = Form(...), db: Session = Depends(get_db)):
    evening = Evening(event_date=date.fromisoformat(event_date))
    db.add(evening)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/admin?error=Speelavond+met+deze+datum+bestaat+al", status_code=303)
    return RedirectResponse(f"/evenings/{evening.id}", status_code=303)


@app.post("/evenings/{evening_id}/delete")
def delete_evening(evening_id: int, db: Session = Depends(get_db)):
    evening = ensure_evening(db, evening_id)
    db.delete(evening)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/evenings/{evening_id}")
def evening_detail(request: Request, evening_id: int, error: str | None = None, db: Session = Depends(get_db)):
    evening = db.execute(
        select(Evening)
        .options(
            joinedload(Evening.attendances).joinedload(Attendance.player),
            joinedload(Evening.matches).joinedload(Match.player1),
            joinedload(Evening.matches).joinedload(Match.player2),
            joinedload(Evening.matches).joinedload(Match.stats),
            joinedload(Evening.groups),
        )
        .where(Evening.id == evening_id)
    ).unique().scalar_one_or_none()
    if not evening:
        raise HTTPException(404)

    players = db.scalars(select(Player).where(Player.active.is_(True)).order_by(Player.name)).all()
    grouped_rows = grouped_rankings_for_evening(db, evening.id) if evening.groups else {}
    evening_highlights = highlights_overview(db, evening.id)
    return templates.TemplateResponse(
        "evening_detail.html",
        {
            "request": request,
            "evening": evening,
            "players": players,
            "grouped_rows": grouped_rows,
            "evening_highlights": evening_highlights,
            "error": error,
            "match_phases": MatchPhase,
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
    try:
        create_groups_for_evening(db, evening)
        db.commit()
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/evenings/{evening_id}?error={quote_plus(str(exc))}", status_code=303)


@app.post("/evenings/{evening_id}/knockout")
def generate_knockout(evening_id: int, db: Session = Depends(get_db)):
    evening = ensure_evening(db, evening_id)
    try:
        create_knockout(db, evening)
        db.commit()
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(f"/evenings/{evening_id}?error={quote_plus(str(exc))}", status_code=303)


@app.post("/evenings/{evening_id}/matches/bulk")
async def submit_bulk_results(evening_id: int, request: Request, db: Session = Depends(get_db)):
    ensure_evening(db, evening_id)
    form = await request.form()
    data = dict(form)

    match_ids = {int(key.split("_")[1]) for key in data if key.startswith("legs1_")}

    for match_id in match_ids:
        legs1 = int(data.get(f"legs1_{match_id}", 0) or 0)
        legs2 = int(data.get(f"legs2_{match_id}", 0) or 0)
        match = save_match_result(db, match_id, legs1, legs2)
        high1 = int(data.get(f"high1_{match_id}", 0) or 0)
        high2 = int(data.get(f"high2_{match_id}", 0) or 0)
        one80_1 = int(data.get(f"one80_1_{match_id}", 0) or 0)
        one80_2 = int(data.get(f"one80_2_{match_id}", 0) or 0)
        fast1 = int(data.get(f"fast1_{match_id}", 0) or 0)
        fast2 = int(data.get(f"fast2_{match_id}", 0) or 0)
        save_match_player_stats(db, match.id, match.evening_id, match.player1_id, high1, one80_1, fast1)
        save_match_player_stats(db, match.id, match.evening_id, match.player2_id, high2, one80_2, fast2)

    evening = db.get(Evening, evening_id)
    if evening:
        maybe_progress_knockout(db, evening)
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
    save_match_player_stats(db, match.id, match.evening_id, match.player1_id, high1, one80_1, fast1)
    save_match_player_stats(db, match.id, match.evening_id, match.player2_id, high2, one80_2, fast2)
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
        "background_color": "#121321",
        "theme_color": "#121321",
        "icons": [],
    }
