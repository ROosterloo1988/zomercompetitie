from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from zomercompetitie.models import (
    Attendance,
    Evening,
    EveningStatus,
    Group,
    GroupAssignment,
    Match,
    MatchPhase,
    MatchPlayerStat,
    Player,
    Season,
    SeasonEvening,
    SeasonStatus,
)

KNOCKOUT_POINTS = {
    "presence": 1,
    MatchPhase.QUARTER: 2,
    MatchPhase.SEMI: 3,
    MatchPhase.FINAL: 4,
    "winner": 5,
}


@dataclass
class StandingRow:
    player: Player
    points: int
    leg_diff: int


@dataclass
class HighlightRow:
    player: Player
    high_finishes_100: int
    high_finish_values: list[int]
    one_eighty: int
    fast_legs_15: int
    fast_leg_values: list[int]


@dataclass
class SeasonStandingRow:
    player: Player
    points: int
    leg_diff: int


def ensure_evening(session: Session, evening_id: int) -> Evening:
    evening = session.get(Evening, evening_id)
    if not evening:
        raise ValueError("Speelavond niet gevonden")
    return evening


def evening_lock_state(session: Session, evening: Evening) -> tuple[bool, str | None]:
    if evening.status == EveningStatus.CLOSED:
        return True, "Speelavond is afgesloten en alleen-lezen"

    closed_season_name = session.scalar(
        select(Season.name)
        .join(SeasonEvening, SeasonEvening.season_id == Season.id)
        .where(SeasonEvening.evening_id == evening.id, Season.status == SeasonStatus.CLOSED)
        .limit(1)
    )
    if closed_season_name:
        return True, f"Seizoen '{closed_season_name}' is gearchiveerd; avond is alleen-lezen"

    return False, None


def reset_evening_groups(session: Session, evening: Evening) -> None:
    for match in list(evening.matches):
        session.delete(match)
    for group in list(evening.groups):
        session.delete(group)
    session.flush()


def create_groups_for_evening(session: Session, evening: Evening) -> list[Group]:
    present_players = [a.player for a in evening.attendances if a.present]
    if len(present_players) < 3:
        raise ValueError("Minimaal 3 aanwezigen nodig")

    reset_evening_groups(session, evening)
    history = pair_history(session)

    target_sizes = choose_group_sizes(len(present_players))
    groups = [Group(evening_id=evening.id, name=f"Poule {chr(65+i)}") for i in range(len(target_sizes))]
    session.add_all(groups)
    session.flush()

    buckets: list[list[Player]] = [[] for _ in target_sizes]
    unassigned = present_players[:]
    random.shuffle(unassigned)

    while unassigned:
        player = unassigned.pop(0)
        best_idx = min(
            range(len(target_sizes)),
            key=lambda i: placement_cost(player.id, buckets[i], history) + (1000 if len(buckets[i]) >= target_sizes[i] else 0),
        )
        buckets[best_idx].append(player)

    for group, players in zip(groups, buckets, strict=True):
        for p in players:
            session.add(GroupAssignment(group_id=group.id, player_id=p.id))
        create_group_matches(session, evening.id, group, players, evening.board_count)

    evening.status = EveningStatus.GROUP_ACTIVE
    return groups


def choose_group_sizes(total_players: int) -> list[int]:
    if total_players < 3:
        raise ValueError("Kan geen geldige poules maken voor dit aantal spelers")

    if total_players <= 6:
        return [total_players]

    group_count = (total_players + 5) // 6
    base_size = total_players // group_count
    remainder = total_players % group_count

    groups = [base_size + (1 if idx < remainder else 0) for idx in range(group_count)]
    if any(size < 3 or size > 6 for size in groups):
        raise ValueError("Kan geen geldige poules maken voor dit aantal spelers")
    return groups






GROUP_MATCH_TEMPLATES: dict[int, list[tuple[int, int]]] = {
    3: [(0, 1), (2, 0), (1, 2), (1, 0), (0, 2), (2, 1)],
    4: [(0, 3), (1, 2), (0, 2), (3, 1), (0, 1), (2, 3)],
    5: [(0, 4), (1, 3), (2, 4), (0, 3), (1, 2), (2, 3), (4, 1), (0, 2), (3, 4), (0, 1)],
    6: [(0, 5), (1, 4), (2, 3), (0, 4), (5, 3), (1, 2), (0, 3), (4, 2), (5, 1), (0, 2), (3, 1), (4, 5), (0, 1), (2, 5), (3, 4)],
}


def create_group_matches(session: Session, evening_id: int, group: Group, players: list[Player], board_count: int) -> None:
    template = GROUP_MATCH_TEMPLATES.get(len(players))
    if not template:
        raise ValueError("Ongeldige poulegrootte voor wedstrijdschema")

    for idx, (a, b) in enumerate(template):
        p1, p2 = players[a], players[b]
        session.add(
            Match(
                evening_id=evening_id,
                phase=MatchPhase.GROUP,
                group_id=group.id,
                player1_id=p1.id,
                player2_id=p2.id,
                bracket_order=idx,
                board_number=(idx % max(board_count, 1)) + 1,
            )
        )

def pair_history(session: Session) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = defaultdict(int)
    rows = session.execute(select(Match.player1_id, Match.player2_id)).all()
    for a, b in rows:
        pair = tuple(sorted((a, b)))
        counts[pair] += 1
    return counts


def placement_cost(player_id: int, bucket: list[Player], history: dict[tuple[int, int], int]) -> int:
    return sum(history[tuple(sorted((player_id, p.id)))] for p in bucket)


def save_match_result(session: Session, match_id: int, legs1: int, legs2: int) -> Match:
    match = session.get(Match, match_id)
    if not match:
        raise ValueError("Wedstrijd niet gevonden")
    match.legs_player1 = legs1
    match.legs_player2 = legs2
    if legs1 == legs2:
        match.winner_id = None
    else:
        match.winner_id = match.player1_id if legs1 > legs2 else match.player2_id
    return match


def validate_evening_groups(session: Session, evening: Evening) -> None:
    groups = session.execute(
        select(Group).options(joinedload(Group.assignments)).where(Group.evening_id == evening.id).order_by(Group.id)
    ).scalars().unique().all()
    if not groups:
        raise ValueError("Poules zijn ongeldig, genereer opnieuw")
    if any(len(group.assignments) < 3 for group in groups):
        raise ValueError("Poules zijn ongeldig, genereer opnieuw")


def create_knockout(session: Session, evening: Evening) -> list[Match]:
    validate_evening_groups(session, evening)
    group_rankings = group_rankings_for_evening(session, evening.id)
    seed_players = [row[0] for row in group_rankings]
    bracket_size = 8 if len(seed_players) >= 8 else 4
    seed_players = seed_players[:bracket_size]

    if len(seed_players) < 4:
        raise ValueError("Minimaal 4 spelers nodig voor knock-out")

    pair_indices = [(0, bracket_size - 1), (1, bracket_size - 2)]
    if bracket_size == 8:
        pair_indices += [(2, 5), (3, 4)]
    phase = MatchPhase.SEMI if bracket_size == 4 else MatchPhase.QUARTER

    matches = []
    for idx, (a, b) in enumerate(pair_indices):
        p1, p2 = seed_players[a], seed_players[b]
        m = Match(
            evening_id=evening.id,
            phase=phase,
            player1_id=p1.id,
            player2_id=p2.id,
            bracket_order=idx,
            board_number=(idx % max(evening.board_count, 1)) + 1,
        )
        session.add(m)
        matches.append(m)

    evening.status = EveningStatus.KNOCKOUT_ACTIVE
    return matches


def maybe_progress_knockout(session: Session, evening: Evening) -> None:
    matches = session.scalars(select(Match).where(Match.evening_id == evening.id)).all()
    by_phase: dict[MatchPhase, list[Match]] = defaultdict(list)
    for m in matches:
        by_phase[m.phase].append(m)

    if by_phase[MatchPhase.QUARTER] and not by_phase[MatchPhase.SEMI]:
        if all(m.winner_id for m in by_phase[MatchPhase.QUARTER]):
            winners = [m.winner_id for m in sorted(by_phase[MatchPhase.QUARTER], key=lambda x: x.bracket_order)]
            for idx in range(0, len(winners), 2):
                session.add(
                    Match(
                        evening_id=evening.id,
                        phase=MatchPhase.SEMI,
                        player1_id=winners[idx],
                        player2_id=winners[idx + 1],
                        bracket_order=idx // 2,
                        board_number=(idx // 2 % max(evening.board_count, 1)) + 1,
                    )
                )

    if by_phase[MatchPhase.SEMI] and not by_phase[MatchPhase.FINAL]:
        if all(m.winner_id for m in by_phase[MatchPhase.SEMI]):
            winners = [m.winner_id for m in sorted(by_phase[MatchPhase.SEMI], key=lambda x: x.bracket_order)]
            session.add(
                Match(
                    evening_id=evening.id,
                    phase=MatchPhase.FINAL,
                    player1_id=winners[0],
                    player2_id=winners[1],
                    bracket_order=0,
                    board_number=1,
                )
            )

    finals = by_phase[MatchPhase.FINAL]
    if finals and all(m.winner_id for m in finals):
        evening.status = EveningStatus.CLOSED


def group_rankings_for_evening(session: Session, evening_id: int) -> list[tuple[Player, int, int]]:
    grouped = grouped_rankings_for_evening(session, evening_id)
    flat: list[tuple[Player, int, int]] = []
    for rows in grouped.values():
        flat.extend(rows)
    flat.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return flat


def grouped_rankings_for_evening(session: Session, evening_id: int) -> dict[str, list[tuple[Player, int, int]]]:
    evening = session.execute(
        select(Evening)
        .options(joinedload(Evening.groups).joinedload(Group.assignments).joinedload(GroupAssignment.player), joinedload(Evening.matches))
        .where(Evening.id == evening_id)
    ).unique().scalar_one()

    grouped_rows: dict[str, list[tuple[Player, int, int]]] = {}
    for group in sorted(evening.groups, key=lambda g: g.name):
        stats = {a.player_id: {"points": 0, "legs_for": 0, "legs_against": 0} for a in group.assignments}
        group_matches = [m for m in evening.matches if m.group_id == group.id]
        for m in group_matches:
            stats[m.player1_id]["legs_for"] += m.legs_player1
            stats[m.player1_id]["legs_against"] += m.legs_player2
            stats[m.player2_id]["legs_for"] += m.legs_player2
            stats[m.player2_id]["legs_against"] += m.legs_player1
            if m.winner_id:
                stats[m.winner_id]["points"] += 2

        rows = []
        for a in group.assignments:
            s = stats[a.player_id]
            rows.append((a.player, s["points"], s["legs_for"] - s["legs_against"]))
        rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
        grouped_rows[group.name] = rows

    return grouped_rows


def overall_standings(session: Session) -> list[StandingRow]:
    players = session.scalars(select(Player).where(Player.active.is_(True))).all()
    standings = []

    for p in players:
        points = 0
        leg_diff = 0

        attendances = session.scalars(select(Attendance).where(Attendance.player_id == p.id, Attendance.present.is_(True))).all()
        points += len(attendances) * KNOCKOUT_POINTS["presence"]

        matches = session.scalars(select(Match).where(Match.player1_id == p.id)).all() + session.scalars(
            select(Match).where(Match.player2_id == p.id)
        ).all()

        for m in matches:
            if m.player1_id == p.id:
                leg_diff += m.legs_player1 - m.legs_player2
            else:
                leg_diff += m.legs_player2 - m.legs_player1

            if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != p.id:
                points += KNOCKOUT_POINTS[m.phase]
            if m.phase == MatchPhase.FINAL and m.winner_id == p.id:
                points += KNOCKOUT_POINTS["winner"]

        standings.append(StandingRow(player=p, points=points, leg_diff=leg_diff))

    standings.sort(key=lambda x: (x.points, x.leg_diff), reverse=True)
    return standings


def save_match_player_stats(
    session: Session,
    match_id: int,
    evening_id: int,
    player_id: int,
    high_100: int,
    high_100_values: list[int],
    one_eighty: int,
    fast_legs: int,
    fast_legs_values: list[int],
) -> None:
    row = session.scalars(
        select(MatchPlayerStat).where(
            MatchPlayerStat.match_id == match_id,
            MatchPlayerStat.player_id == player_id,
        )
    ).first()
    if not row:
        row = MatchPlayerStat(match_id=match_id, evening_id=evening_id, player_id=player_id)
        session.add(row)
    row.high_finishes_100 = max(high_100, 0)
    row.high_finishes_100_values = serialize_stat_values(high_100_values)
    row.one_eighty = max(one_eighty, 0)
    row.fast_legs_15 = max(fast_legs, 0)
    row.fast_legs_15_values = serialize_stat_values(fast_legs_values)


def serialize_stat_values(values: list[int]) -> str:
    return ",".join(str(v) for v in values)


def parse_stat_values(raw: str, minimum: int | None = None, maximum: int | None = None) -> list[int]:
    items: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        value = int(token)
        if minimum is not None and value < minimum:
            continue
        if maximum is not None and value > maximum:
            continue
        items.append(value)
    return items


def highlights_overview(session: Session, evening_id: int | None = None) -> list[HighlightRow]:
    stmt = select(MatchPlayerStat)
    if evening_id is not None:
        stmt = stmt.where(MatchPlayerStat.evening_id == evening_id)

    totals: dict[int, HighlightRow] = {}
    for stat in session.scalars(stmt).all():
        if stat.player_id not in totals:
            totals[stat.player_id] = HighlightRow(
                player=stat.player,
                high_finishes_100=0,
                high_finish_values=[],
                one_eighty=0,
                fast_legs_15=0,
                fast_leg_values=[],
            )
        row = totals[stat.player_id]
        row.high_finishes_100 += stat.high_finishes_100
        row.high_finish_values.extend(parse_stat_values(stat.high_finishes_100_values, minimum=100))
        row.one_eighty += stat.one_eighty
        row.fast_legs_15 += stat.fast_legs_15
        row.fast_leg_values.extend(parse_stat_values(stat.fast_legs_15_values, minimum=1, maximum=15))

    rows = list(totals.values())
    rows.sort(key=lambda r: (r.high_finishes_100 + r.one_eighty + r.fast_legs_15, r.high_finishes_100, r.one_eighty), reverse=True)
    return rows


def ensure_default_season(session: Session) -> Season:
    season = session.scalars(select(Season).order_by(Season.id.desc())).first()
    if season:
        return season
    season = Season(name=f"Seizoen {datetime.utcnow().year}")
    session.add(season)
    session.flush()
    return season


def assign_evening_to_open_season(session: Session, evening: Evening) -> None:
    season = session.scalars(select(Season).where(Season.status == SeasonStatus.OPEN).order_by(Season.id.desc())).first()
    if not season:
        season = ensure_default_season(session)
    exists = session.scalars(select(SeasonEvening).where(SeasonEvening.evening_id == evening.id)).first()
    if not exists:
        session.add(SeasonEvening(season_id=season.id, evening_id=evening.id))


def close_season(session: Session, season_id: int) -> Season:
    season = session.get(Season, season_id)
    if not season:
        raise ValueError("Seizoen niet gevonden")
    season.status = SeasonStatus.CLOSED
    season.closed_at = datetime.utcnow()
    return season


def season_standings(session: Session, season_id: int) -> list[SeasonStandingRow]:
    links = session.scalars(select(SeasonEvening).where(SeasonEvening.season_id == season_id)).all()
    evening_ids = [l.evening_id for l in links]
    if not evening_ids:
        return []

    players = session.scalars(select(Player).where(Player.active.is_(True))).all()
    rows: list[SeasonStandingRow] = []
    for p in players:
        points = 0
        leg_diff = 0
        attendances = session.scalars(
            select(Attendance).where(Attendance.player_id == p.id, Attendance.evening_id.in_(evening_ids), Attendance.present.is_(True))
        ).all()
        points += len(attendances) * KNOCKOUT_POINTS["presence"]

        matches = session.scalars(select(Match).where(Match.evening_id.in_(evening_ids), Match.player1_id == p.id)).all() + session.scalars(
            select(Match).where(Match.evening_id.in_(evening_ids), Match.player2_id == p.id)
        ).all()
        for m in matches:
            if m.player1_id == p.id:
                leg_diff += m.legs_player1 - m.legs_player2
            else:
                leg_diff += m.legs_player2 - m.legs_player1
            if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != p.id:
                points += KNOCKOUT_POINTS[m.phase]
            if m.phase == MatchPhase.FINAL and m.winner_id == p.id:
                points += KNOCKOUT_POINTS["winner"]
        if points or leg_diff:
            rows.append(SeasonStandingRow(player=p, points=points, leg_diff=leg_diff))
    rows.sort(key=lambda x: (x.points, x.leg_diff), reverse=True)
    return rows
