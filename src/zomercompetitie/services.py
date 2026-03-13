from __future__ import annotations

import itertools
import random
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from zomercompetitie.models import (
    Attendance,
    Evening,
    EveningStatus,
    Group,
    GroupAssignment,
    Match,
    MatchPhase,
    Player,
    PlayerStat,
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


def ensure_evening(session: Session, evening_id: int) -> Evening:
    evening = session.get(Evening, evening_id)
    if not evening:
        raise ValueError("Speelavond niet gevonden")
    return evening


def create_groups_for_evening(session: Session, evening: Evening) -> list[Group]:
    present_players = [a.player for a in evening.attendances if a.present]
    if len(present_players) < 3:
        raise ValueError("Minimaal 3 aanwezigen nodig")

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
    best: list[int] | None = None
    best_score = 10**9
    for n3 in range(total_players // 3 + 1):
        for n4 in range(total_players // 4 + 1):
            for n5 in range(total_players // 5 + 1):
                for n6 in range(total_players // 6 + 1):
                    s = 3 * n3 + 4 * n4 + 5 * n5 + 6 * n6
                    if s != total_players:
                        continue
                    groups = [3] * n3 + [4] * n4 + [5] * n5 + [6] * n6
                    score = len(groups) * 10 + n3
                    if score < best_score:
                        best_score = score
                        best = groups
    if not best:
        raise ValueError("Kan geen geldige poules maken voor dit aantal spelers")
    random.shuffle(best)
    return best


def create_group_matches(session: Session, evening_id: int, group: Group, players: list[Player], board_count: int) -> None:
    pairings = list(itertools.combinations(players, 2))
    if len(players) == 3:
        pairings = pairings * 2

    for idx, (p1, p2) in enumerate(pairings):
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


def create_knockout(session: Session, evening: Evening) -> list[Match]:
    group_rankings = group_rankings_for_evening(session, evening.id)
    seed_players = [row[0] for row in group_rankings]
    bracket_size = 4 if len(seed_players) <= 6 else 8
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
    evening = session.execute(
        select(Evening)
        .options(joinedload(Evening.groups).joinedload(Group.assignments).joinedload(GroupAssignment.player), joinedload(Evening.matches))
        .where(Evening.id == evening_id)
    ).unique().scalar_one()

    rows: list[tuple[Player, int, int]] = []
    for group in evening.groups:
        stats = {a.player_id: {"points": 0, "legs_for": 0, "legs_against": 0} for a in group.assignments}
        group_matches = [m for m in evening.matches if m.group_id == group.id]
        for m in group_matches:
            stats[m.player1_id]["legs_for"] += m.legs_player1
            stats[m.player1_id]["legs_against"] += m.legs_player2
            stats[m.player2_id]["legs_for"] += m.legs_player2
            stats[m.player2_id]["legs_against"] += m.legs_player1
            if m.winner_id:
                stats[m.winner_id]["points"] += 2

        group_rows = []
        for a in group.assignments:
            s = stats[a.player_id]
            group_rows.append((a.player, s["points"], s["legs_for"] - s["legs_against"]))
        group_rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
        rows.extend(group_rows)

    rows.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return rows


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


def upsert_player_stats(session: Session, evening_id: int, player_id: int, high_100: int, one_eighty: int, fast_legs: int) -> None:
    stmt: Select[tuple[PlayerStat]] = select(PlayerStat).where(
        PlayerStat.evening_id == evening_id,
        PlayerStat.player_id == player_id,
    )
    row = session.scalars(stmt).first()
    if not row:
        row = PlayerStat(evening_id=evening_id, player_id=player_id)
        session.add(row)
    row.high_finishes_100 = high_100
    row.one_eighty = one_eighty
    row.fast_legs_15 = fast_legs
