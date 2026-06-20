from __future__ import annotations

import os
import json
from importlib.metadata import PackageNotFoundError, version
from datetime import date
from urllib.parse import quote_plus

# NIEUW: BackgroundTasks, WebSocket en WebSocketDisconnect toegevoegd
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

# Authenticatie imports
from starlette.middleware.sessions import SessionMiddleware
import bcrypt

from zomercompetitie.db import Base, SessionLocal, engine, run_sqlite_migrations
from zomercompetitie.models import Attendance, Evening, Match, MatchPhase, MatchPlayerStat, Player, Season, SeasonEvening, SeasonStatus, AdminUser, SystemSetting, Group, GroupAssignment
from zomercompetitie.services import (
    assign_evening_to_open_season,
    close_season,
    create_groups_for_evening,
    create_knockout,
    ensure_default_season,
    ensure_evening,
    evening_lock_state,
    grouped_rankings_for_evening,
    highlights_overview,
    maybe_progress_knockout,
    overall_standings,
    parse_stat_values,
    save_match_player_stats,
    save_match_result,
    StaleMatchError,
    season_standings,
    get_group_options_display,
    add_late_player_to_group,
    add_late_koppel_to_group,
    entity_member_ids,
    build_player_name_id_map,
)
from zomercompetitie.update_checker import check_github_update

app = FastAPI(title="Zomercompetitie")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- WEBSOCKETS ZENDMAST (REAL-TIME MAGIE) ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[tuple[WebSocket, str]] = []

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections.append((websocket, client_id))

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [
            (ws, cid)
            for (ws, cid) in self.active_connections
            if ws != websocket
        ]

    async def broadcast(self, message: str, exclude_client_id: str | None = None):
        for ws, cid in list(self.active_connections):

            # Skip degene die zelf de wijziging deed
            if exclude_client_id and cid == exclude_client_id:
                continue

            try:
                await ws.send_text(message)

            except Exception:
                if (ws, cid) in self.active_connections:
                    self.active_connections.remove((ws, cid))


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id = websocket.query_params.get("client_id", "unknown")

    await manager.connect(websocket, client_id)

    try:
        while True:
            message = await websocket.receive_text()

            # Live invoer doorsturen naar andere clients.
            # Dit slaat nog niets op in de database.
            await manager.broadcast(message, exclude_client_id=client_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ---------------------------------------------

# --- BEVEILIGING & TV SETTINGS ---
secret_key = os.getenv("SECRET_KEY", "fallback-secret-als-env-faalt")
app.add_middleware(SessionMiddleware, secret_key=secret_key, max_age=31536000) # Bewaar inlog voor 1 jaar

class NotAuthorizedException(Exception):
    pass

@app.exception_handler(NotAuthorizedException)
def auth_exception_handler(request: Request, exc: NotAuthorizedException):
    request.session["flash_error"] = "Je moet ingelogd zijn als beheerder om dit te doen."
    return RedirectResponse(url="/login", status_code=303)

def require_admin(request: Request):
    if not request.session.get("admin_logged_in"):
        raise NotAuthorizedException()
    return True

def get_tv_settings(db: Session):
    settings = db.scalars(select(SystemSetting)).all()
    tv_dict = {"board1": "", "board2": ""}
    for s in settings:
        tv_dict[s.key] = s.value
    return tv_dict
# ----------------------------------


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def app_version() -> str:
    try:
        return version("zomercompetitie")
    except PackageNotFoundError:
        return "0.0.0"

@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    run_sqlite_migrations()
    
    db = SessionLocal()
    try:
        admin_user = db.scalar(select(AdminUser).limit(1))
        env_password = os.getenv("ADMIN_PASSWORD")
        
        if env_password:
            password_bytes = env_password.encode('utf-8')
            if not admin_user:
                hashed_pw = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')
                db.add(AdminUser(password_hash=hashed_pw))
                db.commit()
            else:
                hashed_pw = bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')
                admin_user.password_hash = hashed_pw
                db.commit()
    finally:
        db.close()

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def match_sort_key(match: Match) -> tuple[int, int, int, int]:
    phase_order = {MatchPhase.GROUP: 0, MatchPhase.QUARTER: 1, MatchPhase.SEMI: 2, MatchPhase.FINAL: 3}
    group_order = match.group_id if match.group_id is not None else 9999
    
    # 🚀 FIX: Wissel bracket_order en group_order om in de return!
    # Door bracket_order (de 'speelronde') vóór de poule te zetten, 
    # worden wedstrijden perfect geritst: A1, B1, C1, A2, B2, C2...
    return (phase_order.get(match.phase, 9), match.bracket_order, group_order, match.id)

def ensure_evening_editable(db: Session, evening: Evening) -> None:
    locked, reason = evening_lock_state(db, evening)
    if locked:
        raise ValueError(reason or "Speelavond is alleen-lezen")

def match_status(match: Match) -> str:
    return "completed" if match.winner_id is not None or (match.legs_player1 + match.legs_player2) > 0 else "pending"

def match_phase_label(match: Match) -> str:
    if match.phase == MatchPhase.GROUP:
        if match.group and match.group.name:
            suffix = match.group.name.replace("Poule", "").strip()
            return f"Poule {suffix}"
        if match.group_id:
            return f"Poule {match.group_id}"
    return match.phase.value
    
def is_valid_finish(score: int) -> bool:
    """Controleert of een getal een geldige dart-finish is."""
    if score > 170:
        return False
    # De beruchte 'bogey numbers' onder de 170 die onmogelijk zijn
    if score in {169, 168, 166, 165, 163, 162, 159}:
        return False
    return True


def active_players_by_name(db: Session) -> dict[str, Player]:
    """Naam -> actieve Player, om ingevoerde namen aan de originele speler-id te koppelen."""
    return {p.name.strip(): p for p in db.scalars(select(Player).where(Player.active.is_(True))).all()}


def persist_match_from_form(
    db: Session,
    match_id: int,
    data: dict,
    active_players: dict[str, Player],
    expected_version: int | None = None,
) -> Match:
    """
    Slaat de legs én de statistieken van één wedstrijd op. Wordt gebruikt door zowel
    de losse opslag (per wedstrijd) als de bulk-opslag, zodat er maar één correcte
    code-pad is. Veldnamen zijn geprefixt met het match-id, bijv. 'legs1_{id}',
    'high_{id}_{naam}', 'one80_{id}_{naam}', 'fast1_values_{id}'.

    Gooit StaleMatchError als de meegestuurde versie verouderd is, en ValueError bij
    een ongeldige uitslag (zoals gelijkspel met legs > 0).
    """
    legs1 = int(data.get(f"legs1_{match_id}", 0) or 0)
    legs2 = int(data.get(f"legs2_{match_id}", 0) or 0)

    if legs1 == legs2 and (legs1 > 0 or legs2 > 0):
        raise ValueError("Gelijkspel is niet toegestaan. Controleer de ingevoerde uitslagen.")

    # Kan StaleMatchError / ValueError gooien; bumpt row_version bij succes.
    match = save_match_result(db, match_id, legs1, legs2, expected_version=expected_version)

    p1_names = [n.strip() for n in match.player1.name.split("&")] if match.player1 else []
    p2_names = [n.strip() for n in match.player2.name.split("&")] if match.player2 else []

    fast1_values = parse_stat_values(str(data.get(f"fast1_values_{match_id}", "")).strip(), minimum=1, maximum=15)
    fast2_values = parse_stat_values(str(data.get(f"fast2_values_{match_id}", "")).strip(), minimum=1, maximum=15)

    db.query(MatchPlayerStat).filter(MatchPlayerStat.match_id == match.id).delete()
    db.flush()

    for name in p1_names:
        real_p = active_players.get(name)
        pid = real_p.id if real_p else match.player1_id
        raw_high = str(data.get(f"high_{match_id}_{name}", data.get(f"high1_values_{match_id}", "")))
        high_list = [x for x in parse_stat_values(raw_high, minimum=100) if is_valid_finish(x)]
        one80 = int(data.get(f"one80_{match_id}_{name}", data.get(f"one80_1_{match_id}", 0)) or 0)
        if high_list or one80 or fast1_values:
            save_match_player_stats(db, match.id, match.evening_id, pid, len(high_list), high_list, one80, len(fast1_values), fast1_values)

    for name in p2_names:
        real_p = active_players.get(name)
        pid = real_p.id if real_p else match.player2_id
        raw_high = str(data.get(f"high_{match_id}_{name}", data.get(f"high2_values_{match_id}", "")))
        high_list = [x for x in parse_stat_values(raw_high, minimum=100) if is_valid_finish(x)]
        one80 = int(data.get(f"one80_{match_id}_{name}", data.get(f"one80_2_{match_id}", 0)) or 0)
        if high_list or one80 or fast2_values:
            save_match_player_stats(db, match.id, match.evening_id, pid, len(high_list), high_list, one80, len(fast2_values), fast2_values)

    return match

# --- INLOG ROUTES ---
@app.get("/login")
def login_form(request: Request):
    error = request.session.pop("flash_error", None)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": error})

@app.post("/login")
def login_submit(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(AdminUser).limit(1))
    if not user:
        request.session["flash_error"] = "Systeemfout: Geen beheerder gevonden"
        return RedirectResponse("/login", status_code=303)
    try:
        is_valid = bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8'))
    except Exception:
        is_valid = False
    if not is_valid:
        request.session["flash_error"] = "Ongeldig wachtwoord"
        return RedirectResponse("/login", status_code=303)
    
    request.session["admin_logged_in"] = True
    return RedirectResponse("/admin", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
# --------------------

@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    ensure_default_season(db)
    db.commit()
    is_tv = request.query_params.get("tv") == "1"
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    standings = overall_standings(db)
    highlights = highlights_overview(db)
    
    # 🚀 NIEUW: Haal de laatste avond op INCLUSIEF de aanwezige spelers (voor de TV-lijst)
    latest = db.execute(
        select(Evening)
        .options(joinedload(Evening.attendances).joinedload(Attendance.player))
        .order_by(Evening.event_date.desc())
    ).unique().scalars().first()
    
    latest_matches = (
        sorted(
            db.scalars(
                select(Match)
                .options(
                    joinedload(Match.player1), 
                    joinedload(Match.player2), 
                    joinedload(Match.group),
                    joinedload(Match.stats),
                    joinedload(Match.scorekeeper)
                )
                .where(Match.evening_id == latest.id)
            ).unique().all(),
            key=match_sort_key,
        )
        if latest
        else []
    )
    
    latest_groups = grouped_rankings_for_evening(db, latest.id) if latest and latest.groups else {}
    latest_highlights = highlights_overview(db, latest.id) if latest else []
    seasons = db.scalars(select(Season).order_by(Season.id.desc())).all()
    tv_settings = get_tv_settings(db)

    return templates.TemplateResponse(request, 
        "dashboard.html",
        {
            "request": request,
            "is_tv": is_tv,
            "tv_settings": tv_settings,
            "evenings": evenings,
            "standings": standings,
            "latest_matches": latest_matches,
            "latest": latest,
            "latest_groups": latest_groups,
            "highlights": highlights,
            "latest_highlights": latest_highlights,
            "seasons": seasons,
            "match_phase_label": match_phase_label,
            "match_status": match_status,
            "is_admin": request.session.get("admin_logged_in", False)
        },
    )

@app.post("/players")
def create_player(request: Request, background_tasks: BackgroundTasks, name: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    db.add(Player(name=name.strip()))
    try:
        db.commit()
        background_tasks.add_task(manager.broadcast, "update")
    except IntegrityError:
        db.rollback()
        request.session["flash_error"] = f"Speler '{name}' bestaat al."
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/admin", status_code=303)

@app.post("/players/{player_id}/toggle")
def toggle_player(
    request: Request,
    player_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    is_ajax = request.headers.get("x-requested-with") == "fetch"

    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)

    player.active = not player.active
    db.commit()

    background_tasks.add_task(
    manager.broadcast,
    json.dumps({
        "type": "player_active_update",
        "player_id": player.id,
        "active": player.active,
    }),
)

    if is_ajax:
        return JSONResponse({
            "ok": True,
            "player_id": player.id,
            "active": player.active,
        })

    return RedirectResponse("/admin", status_code=303)

@app.post("/players/{player_id}/update")
def update_player(request: Request, player_id: int, background_tasks: BackgroundTasks, name: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    player.name = name.strip()
    try:
        db.commit()
        background_tasks.add_task(manager.broadcast, "update")
    except IntegrityError:
        db.rollback()
        request.session["flash_error"] = f"De naam '{name}' is al in gebruik."
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/admin", status_code=303)

@app.post("/players/{player_id}/delete")
def delete_player(request: Request, player_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(404)
    matches_count = db.scalar(select(func.count(Match.id)).where((Match.player1_id == player_id) | (Match.player2_id == player_id))) or 0
    if matches_count > 0:
        player.active = False
        db.commit()
        background_tasks.add_task(manager.broadcast, "update")
        request.session["flash_error"] = f"Kan '{player.name}' niet wissen omdat deze wedstrijden heeft. De speler is in plaats daarvan op inactief gezet."
        return RedirectResponse("/admin", status_code=303)
    db.query(Attendance).filter(Attendance.player_id == player_id).delete()
    db.delete(player)
    db.commit()
    background_tasks.add_task(manager.broadcast, "update")
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin")
def admin(request: Request, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    error = request.session.pop("flash_error", None)
    ensure_default_season(db)
    db.commit()
    
    # 🚀 FILTER: Haal alle spelers op, BEHALVE de tijdelijke koppel-teams (die bevatten een '&')
    players = db.scalars(select(Player).where(Player.name.not_like("% & %")).order_by(Player.name)).all()
    
    evenings = db.scalars(select(Evening).order_by(Evening.event_date.desc())).all()
    seasons = db.scalars(select(Season).order_by(Season.id.desc())).all()
    show_devtools = env_flag("ENABLE_ONTWIKKELTOOLS", True)
    update_info = None
    if env_flag("ENABLE_UPDATE_CHECK", True):
        repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        update_info = check_github_update(repo=repo, current_version=app_version())
    tv_settings = get_tv_settings(db)
    return templates.TemplateResponse(request, 
        "admin.html",
        {
            "request": request,
            "players": players,
            "tv_settings": tv_settings,
            "evenings": evenings,
            "seasons": seasons,
            "error": error,
            "show_devtools": show_devtools,
            "update_info": update_info,
        },
    )

@app.post("/admin/reset")
def reset_test_data(background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    if not env_flag("ENABLE_ONTWIKKELTOOLS", True):
        raise HTTPException(status_code=404)
    db.query(MatchPlayerStat).delete()
    db.query(Match).delete()
    db.query(Attendance).delete()
    db.query(SeasonEvening).delete()
    db.query(Evening).delete()
    db.query(Season).delete()
    db.query(Player).delete()
    db.query(AdminUser).delete()
    db.query(SystemSetting).delete()
    db.commit()
    background_tasks.add_task(manager.broadcast, "update")
    return RedirectResponse("/admin", status_code=303)

@app.post("/evenings")
def create_evening(request: Request, background_tasks: BackgroundTasks, event_date: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    evening = Evening(event_date=date.fromisoformat(event_date))
    db.add(evening)
    try:
        db.flush()
        assign_evening_to_open_season(db, evening)
        db.commit()
        background_tasks.add_task(manager.broadcast, "update")
    except IntegrityError:
        db.rollback()
        request.session["flash_error"] = f"Er bestaat al een speelavond op {event_date}."
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse(f"/evenings/{evening.id}", status_code=303)

@app.post("/evenings/{evening_id}/delete")
def delete_evening(evening_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    evening = ensure_evening(db, evening_id)
    db.delete(evening)
    db.commit()
    background_tasks.add_task(manager.broadcast, "update")
    return RedirectResponse("/admin", status_code=303)

@app.get("/evenings/{evening_id}")
def evening_detail(request: Request, evening_id: int, db: Session = Depends(get_db)):
    error = request.session.pop("flash_error", None)
    
    evening = db.execute(
        select(Evening)
        .options(
            joinedload(Evening.attendances).joinedload(Attendance.player),
            joinedload(Evening.matches).joinedload(Match.player1),
            joinedload(Evening.matches).joinedload(Match.player2),
            joinedload(Evening.matches).joinedload(Match.group),
            joinedload(Evening.matches).joinedload(Match.stats),
            joinedload(Evening.matches).joinedload(Match.scorekeeper),
            joinedload(Evening.groups),
        )
        .where(Evening.id == evening_id)
    ).unique().scalar_one_or_none()
    
    if not evening:
        raise HTTPException(404)

    players = db.scalars(
        select(Player)
        .where(Player.active.is_(True))
        .order_by(Player.name)
    ).all()

    assigned_entities = db.scalars(
        select(Player)
        .join(GroupAssignment, GroupAssignment.player_id == Player.id)
        .join(Group, Group.id == GroupAssignment.group_id)
        .where(Group.evening_id == evening_id)
    ).all()

    name_id_map = build_player_name_id_map(db)
    assigned_single_ids = set()

    for entity in assigned_entities:
        assigned_single_ids.update(entity_member_ids(entity, name_id_map))

    late_available_players = [
        p for p in players
        if p.id not in assigned_single_ids and "&" not in p.name
    ]

    is_koppel_evening = any("&" in entity.name for entity in assigned_entities)

    grouped_rows = grouped_rankings_for_evening(db, evening.id) if evening.groups else {}

    grouped_rows = grouped_rankings_for_evening(db, evening.id) if evening.groups else {}
    evening_highlights = highlights_overview(db, evening.id)
    ordered_matches = sorted(evening.matches, key=lambda match: (match_status(match) == "completed", *match_sort_key(match)))
    has_groups = len(evening.groups) > 0
    has_knockout = any(match.phase in {MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL} for match in evening.matches)
    group_matches = [m for m in evening.matches if m.phase == MatchPhase.GROUP]
    all_groups_finished = len(group_matches) > 0 and all(match_status(m) == "completed" for m in group_matches)
    evening_locked, lock_reason = evening_lock_state(db, evening)
    
    # 🚀 Zorgt ervoor dat we de poule-opties voor single én koppel kunnen laten zien
    present_players = [a for a in evening.attendances if a.present]
    single_options = []
    koppel_options = []
    
    if not has_groups and len(present_players) >= 3:
        from zomercompetitie.services import get_group_options_display
        single_options = get_group_options_display(len(present_players))
        
        # Koppel opties zijn alleen beschikbaar bij even aantal en minimaal 6 spelers (3 koppels)
        if len(present_players) >= 6 and len(present_players) % 2 == 0:
            koppel_options = get_group_options_display(len(present_players) // 2)

    return templates.TemplateResponse(request, 
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
            "match_status": match_status,
            "match_phase_label": match_phase_label,
            "has_groups": has_groups,
            "has_knockout": has_knockout,
            "all_groups_finished": all_groups_finished,
            "single_options": single_options, # 🚀 NIEUW VOOR SINGLE
            "koppel_options": koppel_options, # 🚀 NIEUW VOOR KOPPELS
            "late_available_players": late_available_players,
            "is_koppel_evening": is_koppel_evening,
            "present_players_count": len(present_players), # 🚀 NODIG VOOR SCHERM
            "evening_locked": evening_locked,
            "lock_reason": lock_reason,
            "assigned_entities": assigned_entities,
            "is_admin": request.session.get("admin_logged_in", False)
        },
    )

@app.get("/evenings/{evening_id}/group-options")
def evening_group_options(
    evening_id: int,
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    evening = ensure_evening(db, evening_id)

    has_groups = len(evening.groups) > 0
    present_count = db.scalar(
        select(func.count(Attendance.id)).where(
            Attendance.evening_id == evening_id,
            Attendance.present.is_(True),
        )
    ) or 0

    single_options = []
    koppel_options = []

    if not has_groups and present_count >= 3:
        single_options = get_group_options_display(present_count)

        if present_count >= 6 and present_count % 2 == 0:
            koppel_options = get_group_options_display(present_count // 2)

    return JSONResponse({
        "present_count": present_count,
        "single_options": single_options,
        "koppel_options": koppel_options,
    })

@app.post("/evenings/{evening_id}/attendance")
def update_attendance(
    request: Request,
    evening_id: int,
    background_tasks: BackgroundTasks,
    player_id: int = Form(...),
    present: bool = Form(False),
    client_id: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    is_ajax = request.headers.get("x-requested-with") == "fetch"

    evening = ensure_evening(db, evening_id)

    try:
        ensure_evening_editable(db, evening)
    except ValueError as exc:
        if is_ajax:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

    row = db.scalars(
        select(Attendance).where(
            Attendance.evening_id == evening_id,
            Attendance.player_id == player_id,
        )
    ).first()

    if not row:
        row = Attendance(evening_id=evening_id, player_id=player_id)
        db.add(row)

    row.present = present
    db.commit()

    background_tasks.add_task(
    manager.broadcast,
    json.dumps({
        "type": "attendance_update",
        "player_id": player_id,
        "present": present,
    }),
    client_id,
)

    if is_ajax:
        return JSONResponse({"ok": True, "player_id": player_id, "present": present})

    return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
    
@app.post("/evenings/{evening_id}/groups")
def generate_groups(
    request: Request, 
    evening_id: int, 
    background_tasks: BackgroundTasks, 
    config: str = Form(None), 
    format: str = Form("single"), # 🚀 DEZE MISTE: Hij luistert nu naar de single/koppel schakelaar
    db: Session = Depends(get_db), 
    admin: bool = Depends(require_admin)
):
    evening = ensure_evening(db, evening_id)
    try:
        ensure_evening_editable(db, evening)
        has_knockout = db.scalar(
            select(func.count(Match.id)).where(
                Match.evening_id == evening.id,
                Match.phase.in_([MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL]),
            )
        )
        if has_knockout:
            raise ValueError("Knock-out bestaat al; poules kunnen niet meer opnieuw worden gegenereerd")
        
        custom_sizes = [int(s) for s in config.split(",")] if config else None
        
        # 🚀 Geef het toernooi-formaat (Single of Koppel) door aan de motor
        create_groups_for_evening(db, evening, custom_sizes=custom_sizes, tournament_format=format)
        db.commit()
        
        background_tasks.add_task(manager.broadcast, "update")
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
    except ValueError as exc:
        db.rollback()
        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
        
@app.post("/evenings/{evening_id}/groups/{group_id}/late-player")
def add_late_player(
    request: Request,
    evening_id: int,
    group_id: int,
    background_tasks: BackgroundTasks,
    player_id: int = Form(...),
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    evening = ensure_evening(db, evening_id)
    group = db.get(Group, group_id)
    player = db.get(Player, player_id)

    if not group or not player:
        raise HTTPException(404)

    try:
        ensure_evening_editable(db, evening)
        add_late_player_to_group(db, evening, group, player)
        db.commit()

        background_tasks.add_task(manager.broadcast, "update")
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

    except (ValueError, IntegrityError) as exc:
        db.rollback()
        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)


@app.post("/evenings/{evening_id}/groups/{group_id}/late-koppel")
def add_late_koppel(
    request: Request,
    evening_id: int,
    group_id: int,
    background_tasks: BackgroundTasks,
    player1_id: int = Form(...),
    player2_id: int = Form(...),
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    evening = ensure_evening(db, evening_id)
    group = db.get(Group, group_id)
    player1 = db.get(Player, player1_id)
    player2 = db.get(Player, player2_id)

    if not group or not player1 or not player2:
        raise HTTPException(404)

    try:
        ensure_evening_editable(db, evening)
        add_late_koppel_to_group(db, evening, group, player1, player2)
        db.commit()

        background_tasks.add_task(manager.broadcast, "update")
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

    except (ValueError, IntegrityError) as exc:
        db.rollback()
        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

@app.post("/evenings/{evening_id}/knockout")
def generate_knockout(request: Request, evening_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    evening = ensure_evening(db, evening_id)
    try:
        ensure_evening_editable(db, evening)
        has_knockout = db.scalar(
            select(func.count(Match.id)).where(
                Match.evening_id == evening.id,
                Match.phase.in_([MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL]),
            )
        )
        if has_knockout:
            raise ValueError("Knock-out is al gegenereerd voor deze avond")
        if not evening.groups:
            raise ValueError("Genereer eerst poules")
        create_knockout(db, evening)
        db.commit()
        
        # 🚀 NIEUW: Laat de TV direct overspringen naar de knock-out boom!
        background_tasks.add_task(manager.broadcast, "update")
        
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)
    except ValueError as exc:
        db.rollback()
        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

@app.post("/evenings/{evening_id}/matches/bulk")
async def submit_bulk_results(request: Request, evening_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    """
    Fallback-opslag zonder JavaScript. Verwerkt UITSLUITEND de wedstrijden die de
    client als gewijzigd markeert (verborgen veld 'dirty_matches'), elk met een
    versiecheck. Zo kan een verouderd toestel nooit ongewijzigde wedstrijden van
    andere borden overschrijven. Met JavaScript actief loopt opslaan per wedstrijd
    via /matches/{id}/result; deze route is het vangnet.
    """
    evening = ensure_evening(db, evening_id)

    form = await request.form()
    data = dict(form)

    # Bepaal welke wedstrijden bewust gewijzigd zijn. Zonder die opgave slaan we
    # niets op (veilig: geen blind overschrijven van de hele avond).
    raw_dirty = str(data.get("dirty_matches", "")).strip()
    if raw_dirty:
        dirty_ids = [int(x) for x in raw_dirty.split(",") if x.strip().isdigit()]
    else:
        dirty_ids = []

    if not dirty_ids:
        request.session["flash_error"] = "Er waren geen gewijzigde wedstrijden om op te slaan."
        return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

    active_players = active_players_by_name(db)
    conflicts: list[int] = []
    saved = 0

    for match_id in dirty_ids:
        match = db.get(Match, match_id)
        if not match:
            continue
        raw_ver = data.get(f"version_{match_id}")
        expected_version = int(raw_ver) if raw_ver not in (None, "") else None
        try:
            persist_match_from_form(db, match_id, data, active_players, expected_version=expected_version)
            saved += 1
        except StaleMatchError:
            db.rollback()
            conflicts.append(match_id)
        except ValueError as exc:
            db.rollback()
            request.session["flash_error"] = str(exc)
            return RedirectResponse(f"/evenings/{evening_id}", status_code=303)

    maybe_progress_knockout(db, evening)
    db.commit()
    background_tasks.add_task(manager.broadcast, json.dumps({"type": "score_saved", "evening_id": evening_id}))

    if conflicts:
        request.session["flash_error"] = (
            f"{saved} wedstrijd(en) opgeslagen. {len(conflicts)} wedstrijd(en) waren "
            "intussen door iemand anders gewijzigd en zijn niet overschreven — controleer ze."
        )
    return RedirectResponse(f"/evenings/{evening_id}?next=1", status_code=303)


@app.post("/matches/{match_id}/result")
async def submit_result(request: Request, match_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    """
    Slaat de uitslag + statistieken van ÉÉN wedstrijd op (per-wedstrijd opslag).
    Verwacht JSON of formuliervelden geprefixt met het match-id, plus een optioneel
    'version_{id}' veld voor optimistic locking. Antwoordt met JSON, zodat de client
    een conflict (409) netjes kan afhandelen zonder andere wedstrijden te overschrijven.
    """
    match = db.get(Match, match_id)
    if not match:
        raise HTTPException(404)
    evening = db.get(Evening, match.evening_id)
    if not evening:
        raise HTTPException(404)

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        data = await request.json()
    else:
        data = dict(await request.form())

    raw_ver = data.get(f"version_{match_id}", data.get("version"))
    expected_version = int(raw_ver) if raw_ver not in (None, "") else None

    try:
        match = persist_match_from_form(db, match_id, data, active_players_by_name(db), expected_version=expected_version)
    except StaleMatchError as exc:
        db.rollback()
        current = db.get(Match, match_id)
        return JSONResponse(status_code=409, content={
            "error": "stale",
            "message": "Deze wedstrijd is intussen door iemand anders opgeslagen. De nieuwste waarden zijn geladen.",
            "match_id": match_id,
            "current_version": current.row_version if current else None,
            "legs1": current.legs_player1 if current else None,
            "legs2": current.legs_player2 if current else None,
        })
    except ValueError as exc:
        db.rollback()
        return JSONResponse(status_code=400, content={"error": "invalid", "message": str(exc)})

    maybe_progress_knockout(db, evening)
    db.commit()
    background_tasks.add_task(manager.broadcast, json.dumps({"type": "score_saved", "evening_id": evening.id}))
    return JSONResponse({"ok": True, "match_id": match_id, "new_version": match.row_version})


@app.post("/matches/{match_id}/scorekeeper")
async def update_scorekeeper(
    request: Request,
    match_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: bool = Depends(require_admin),
):
    """Handmatige override van de schrijver voor één wedstrijd (alleen admin)."""
    match = db.get(Match, match_id)
    if not match:
        raise HTTPException(404)

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        data = await request.json()
    else:
        data = dict(await request.form())

    raw = data.get("scorekeeper_id", "")
    if raw in (None, "", "0"):
        match.scorekeeper_id = None
    else:
        player_id = int(raw)
        player = db.get(Player, player_id)
        if not player:
            raise HTTPException(400, "Onbekende speler")
        if player_id in {match.player1_id, match.player2_id}:
            return JSONResponse(status_code=400, content={"error": "Speler speelt zelf mee in deze wedstrijd"})
        match.scorekeeper_id = player_id

    db.commit()
    background_tasks.add_task(manager.broadcast, json.dumps({"type": "score_saved", "evening_id": match.evening_id}))
    return JSONResponse({"ok": True, "match_id": match_id, "scorekeeper_id": match.scorekeeper_id})


@app.post("/seasons")
def create_season(request: Request, background_tasks: BackgroundTasks, name: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    db.add(Season(name=name.strip(), status=SeasonStatus.OPEN))
    try:
        db.commit()
        background_tasks.add_task(manager.broadcast, "update")
    except IntegrityError:
        db.rollback()
        request.session["flash_error"] = f"Seizoen '{name}' bestaat al."
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/admin", status_code=303)

@app.post("/seasons/{season_id}/close")
def close_season_route(request: Request, season_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    try:
        close_season(db, season_id)
    except ValueError as exc:
        request.session["flash_error"] = str(exc)
        return RedirectResponse("/admin", status_code=303)
    db.commit()
    background_tasks.add_task(manager.broadcast, "update")
    return RedirectResponse(f"/seasons/{season_id}", status_code=303)

@app.post("/seasons/{season_id}/delete")
def delete_season(season_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    season = db.get(Season, season_id)
    if not season:
        raise HTTPException(404)
    db.delete(season)
    db.commit()
    background_tasks.add_task(manager.broadcast, "update")
    return RedirectResponse("/admin", status_code=303)

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
    return templates.TemplateResponse(request, 
        "season_detail.html",
        {"request": request, "season": season, "standings": standings, "highlights": highlights},
    )

@app.post("/admin/tv-settings")
def update_tv_settings(request: Request, background_tasks: BackgroundTasks, board1: str = Form(""), board2: str = Form(""), db: Session = Depends(get_db), admin: bool = Depends(require_admin)):
    for key, value in [("board1", board1), ("board2", board2)]:
        setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
        if not setting:
            setting = SystemSetting(key=key, value="")
            db.add(setting)
        setting.value = value.strip().split('/')[-1]
    
    db.commit()
    
    # 🚀 NIEUW: Vuurt het zendmast seintje af als jij nieuwe codes opslaat!
    background_tasks.add_task(manager.broadcast, "update")
    
    return RedirectResponse("/admin", status_code=303)

@app.get("/pwa/manifest.webmanifest")
def manifest():
    return {
        "id": "/",
        "name": "Zomercompetitie",
        "short_name": "Zomercomp",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "description": "Mobiel scorecenter voor poules, knock-outs en seizoenstanden.",
        "lang": "nl-NL",
        "orientation": "portrait-primary",
        "background_color": "#121321",
        "theme_color": "#121321",
        "prefer_related_applications": False,
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
