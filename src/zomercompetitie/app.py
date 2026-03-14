from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from zomercompetitie.db import Base, SessionLocal, engine, run_sqlite_migrations
from zomercompetitie.models import Attendance, Evening, Match, MatchPhase, MatchPlayerStat, Player, Season, SeasonEvening, SeasonStatus
from zomercompetitie.services import (
    assign_evening_to_open_season,
    close_season,
    create_groups_for_evening,
    create_knockout,
    ensure_default_season,
    ensure_evening,
    grouped_rankings_for_evening,
    highlights_overview,
    maybe_progress_knockout,
    overall_standings,
    parse_stat_values,
    save_match_player_stats,
    save_match_result,
    season_standings,
)

app = FastAPI(title="Zomercompetitie")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    run_sqlite_migrations()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    ensure_default_season(db)
    db.commit()
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    standings = overall_standings(db)
    highlights = highlights_overview(db)
    latest = evenings[0] if evenings else None
    latest_matches = (
        db.scalars(select(Match).where(Match.evening_id == latest.id).order_by(Match.phase, Match.bracket_order)).all() if latest else []
    )
    latest_groups = grouped_rankings_for_evening(db, latest.id) if latest and latest.groups else {}
    latest_highlights = highlights_overview(db, latest.id) if latest else []
    seasons = db.scalars(select(Season).order_by(Season.id.desc())).all()
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
            "seasons": seasons,
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


@app.post("/players/{player_id}/delete")
def delete_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    matches_count = db.scalar(select(func.count(Match.id)).where((Match.player1_id == player_id) | (Match.player2_id == player_id))) or 0
    if matches_count > 0:
        player.active = False
        db.commit()
        return RedirectResponse("/admin?error=Speler+heeft+wedstrijden+en+is+gedeactiveerd", status_code=303)
    db.query(Attendance).filter(Attendance.player_id == player_id).delete()
    db.delete(player)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin")
def admin(request: Request, error: str | None = None, db: Session = Depends(get_db)):
    ensure_default_season(db)
    db.commit()
    players = db.scalars(select(Player).order_by(Player.name)).all()
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    seasons = db.scalars(select(Season).order_by(Season.id.desc())).all()
    return templates.TemplateResponse(
        "admin.html", {"request": request, "players": players, "evenings": evenings, "seasons": seasons, "error": error}
    )


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
        db.flush()
        assign_evening_to_open_season(db, evening)
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
    phase_order = {MatchPhase.GROUP: 0, MatchPhase.QUARTER: 1, MatchPhase.SEMI: 2, MatchPhase.FINAL: 3}
    ordered_matches = sorted(
        evening.matches,
        key=lambda m: (phase_order.get(m.phase, 9), m.group_id or 0, m.bracket_order, m.id),
    )
    return templates.TemplateResponse(
        "evening_detail.html",
        {
            "request": request,
            "evening": evening,
            "players": players,
            "grouped_rows": grouped_rows,
            "evening_highlights": evening_highlights,
            "error": error,
            "ordered_matches": ordered_matches,
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
        high1_values = parse_stat_values(str(data.get(f"high1_values_{match_id}", "")), minimum=100)
        high2_values = parse_stat_values(str(data.get(f"high2_values_{match_id}", "")), minimum=100)
        one80_1 = int(data.get(f"one80_1_{match_id}", 0) or 0)
        one80_2 = int(data.get(f"one80_2_{match_id}", 0) or 0)
        fast1_values = parse_stat_values(str(data.get(f"fast1_values_{match_id}", "")), minimum=1, maximum=15)
        fast2_values = parse_stat_values(str(data.get(f"fast2_values_{match_id}", "")), minimum=1, maximum=15)
        save_match_player_stats(
            db,
            match.id,
            match.evening_id,
            match.player1_id,
            len(high1_values),
            high1_values,
            one80_1,
            len(fast1_values),
            fast1_values,
        )
        save_match_player_stats(
            db,
            match.id,
            match.evening_id,
            match.player2_id,
            len(high2_values),
            high2_values,
            one80_2,
            len(fast2_values),
            fast2_values,
        )

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
    high1_values: str = Form(""),
    high2_values: str = Form(""),
    one80_1: int = Form(0),
    one80_2: int = Form(0),
    fast1_values: str = Form(""),
    fast2_values: str = Form(""),
    db: Session = Depends(get_db),
):
    match = save_match_result(db, match_id, legs1, legs2)
    high1_list = parse_stat_values(high1_values, minimum=100)
    high2_list = parse_stat_values(high2_values, minimum=100)
    fast1_list = parse_stat_values(fast1_values, minimum=1, maximum=15)
    fast2_list = parse_stat_values(fast2_values, minimum=1, maximum=15)
    save_match_player_stats(db, match.id, match.evening_id, match.player1_id, len(high1_list), high1_list, one80_1, len(fast1_list), fast1_list)
    save_match_player_stats(db, match.id, match.evening_id, match.player2_id, len(high2_list), high2_list, one80_2, len(fast2_list), fast2_list)
    evening = db.get(Evening, match.evening_id)
    maybe_progress_knockout(db, evening)
    db.commit()
    return RedirectResponse(f"/evenings/{match.evening_id}", status_code=303)


@app.post("/seasons")
def create_season(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(Season(name=name.strip(), status=SeasonStatus.OPEN))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/admin?error=Seizoensnaam+bestaat+al", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/seasons/{season_id}/close")
def close_season_route(season_id: int, db: Session = Depends(get_db)):
    try:
        close_season(db, season_id)
    except ValueError as exc:
        return RedirectResponse(f"/admin?error={quote_plus(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(f"/seasons/{season_id}", status_code=303)


@app.get("/seasons/{season_id}")
def season_detail(request: Request, season_id: int, db: Session = Depends(get_db)):
    season = db.execute(
        select(Season).options(joinedload(Season.evening_links).joinedload(SeasonEvening.evening)).where(Season.id == season_id)
    ).unique().scalar_one_or_none()
    if not season:
        raise HTTPException(404)
    standings = season_standings(db, season_id)
    evening_ids = [link.evening_id for link in season.evening_links]
    highlights = []
    if evening_ids:
        rows = db.scalars(select(MatchPlayerStat).where(MatchPlayerStat.evening_id.in_(evening_ids))).all()
        by_player: dict[int, dict[str, object]] = {}
        for stat in rows:
            entry = by_player.setdefault(
                stat.player_id,
                {"player": stat.player, "high": 0, "one_eighty": 0, "fast": 0, "high_values": [], "fast_values": []},
            )
            entry["high"] += stat.high_finishes_100
            entry["one_eighty"] += stat.one_eighty
            entry["fast"] += stat.fast_legs_15
            entry["high_values"].extend(parse_stat_values(stat.high_finishes_100_values, minimum=100))
            entry["fast_values"].extend(parse_stat_values(stat.fast_legs_15_values, minimum=1, maximum=15))
        highlights = sorted(by_player.values(), key=lambda x: (x["high"] + x["one_eighty"] + x["fast"]), reverse=True)
    return templates.TemplateResponse(
        "season_detail.html",
        {"request": request, "season": season, "standings": standings, "highlights": highlights},
    )


@app.get("/pwa/manifest.webmanifest")
def manifest():
    return {
        "name": "Zomercompetitie",
        "short_name": "Zomercomp",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#121321",
        "theme_color": "#121321",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
