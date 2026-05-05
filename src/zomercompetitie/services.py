from __future__ import annotations

import random
import json
from collections import defaultdict
from functools import cmp_to_key
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
    SystemSetting,
)

KNOCKOUT_POINTS = {
    "presence": 1,
    MatchPhase.QUARTER: 2,
    MatchPhase.SEMI: 3,
    MatchPhase.FINAL: 4,
    "winner": 5,
}

# --- NIEUW: Tabel voor snelle berekening van wedstrijden ---
MATCH_COUNTS = {3: 6, 4: 6, 5: 10, 6: 15}

@dataclass
class StandingRow:
    player: Player
    points: int
    leg_diff: int
    attendance_count: int
    wins: int


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

def get_group_options_display(total_players: int) -> list[dict]:
    """Vindt de meest gebalanceerde combinaties van poules (groottes 3-6)."""
    if total_players < 3:
        return []
    
    results = []
    def find_combos(remaining, current_combo, min_val):
        if remaining == 0:
            # 🚀 DE BALANS-FILTER: Check of het verschil tussen de grootste en kleinste poule maximaal 1 is!
            if max(current_combo) - min(current_combo) <= 1:
                results.append(list(current_combo))
            return
            
        for size in range(min_val, 7):
            if size <= remaining and size >= 3:
                current_combo.append(size)
                find_combos(remaining - size, current_combo, size)
                current_combo.pop()

    find_combos(total_players, [], 3)
    
    options = []
    for config in results:
        total_matches = sum(MATCH_COUNTS[s] for s in config)
        # Sorteer config omhoog voor leesbaarheid: (3, 4) ipv (4, 3)
        config.sort()
        desc = f"{len(config)} poules (groottes: {', '.join(map(str, config))})"
        options.append({
            "config": ",".join(map(str, config)),
            "description": desc,
            "total_matches": total_matches
        })
    
    # Sorteer op aantal wedstrijden (laag naar hoog)
    return sorted(options, key=lambda x: x['total_matches'])
    
# --- START MYSTERIE KOPPEL LOGICA ---

def get_koppel_history(session: Session) -> dict[tuple[int, int], int]:
    """Haalt op wie al met wie als koppel heeft gespeeld."""
    setting = session.scalar(select(SystemSetting).where(SystemSetting.key == "koppel_history"))
    if not setting or not setting.value:
        return defaultdict(int)

    try:
        data = json.loads(setting.value)
        history = defaultdict(int)
        for k, v in data.items():
            id1, id2 = map(int, k.split("-"))
            history[tuple(sorted((id1, id2)))] = v
        return history
    except Exception:
        return defaultdict(int)


def save_koppel_history(session: Session, history: dict[tuple[int, int], int]) -> None:
    setting = session.scalar(select(SystemSetting).where(SystemSetting.key == "koppel_history"))
    if not setting:
        setting = SystemSetting(key="koppel_history", value="")
        session.add(setting)

    setting.value = json.dumps({f"{a}-{b}": v for (a, b), v in history.items()})


def split_koppel_player(player: Player) -> list[str]:
    """Geeft bij 'Jan & Piet' de individuele namen terug."""
    return [name.strip() for name in player.name.split("&") if name.strip()]


def build_player_name_id_map(session: Session) -> dict[str, int]:
    """Mapt actieve singles op naam naar ID."""
    players = session.scalars(select(Player).where(Player.active.is_(True))).all()
    return {p.name.strip(): p.id for p in players}


def entity_member_ids(entity: Player, name_id_map: dict[str, int]) -> list[int]:
    """
    Singles: [speler_id]
    Koppels: [id_speler_1, id_speler_2]
    """
    if "&" not in entity.name:
        return [entity.id]

    ids: list[int] = []
    for name in split_koppel_player(entity):
        player_id = name_id_map.get(name)
        if player_id:
            ids.append(player_id)
    return ids


def individual_pair_history(session: Session) -> dict[tuple[int, int], int]:
    """
    Historie op individueel niveau.

    Werkt voor:
    - singles: Jan vs Piet
    - koppels: Jan & Piet vs Klaas & Henk
    """
    counts: dict[tuple[int, int], int] = defaultdict(int)
    name_id_map = build_player_name_id_map(session)

    matches = session.scalars(
        select(Match).options(
            joinedload(Match.player1),
            joinedload(Match.player2),
            joinedload(Match.evening),
        )
    ).unique().all()

    for match in matches:
        if not match.player1 or not match.player2:
            continue

        side1_ids = entity_member_ids(match.player1, name_id_map)
        side2_ids = entity_member_ids(match.player2, name_id_map)

        for id1 in side1_ids:
            for id2 in side2_ids:
                if id1 == id2:
                    continue

                pair = tuple(sorted((id1, id2)))
                weight = 1

                if match.evening and match.evening.event_date:
                    days_ago = (datetime.now().date() - match.evening.event_date).days

                    if days_ago < 14:
                        weight = 3
                    elif days_ago < 30:
                        weight = 2

                counts[pair] += weight

    return counts

def create_koppels(session: Session, players: list[Player]) -> list[Player]:
    """
    Maakt koppels op basis van wie het minst vaak samen heeft gespeeld.
    """
    history = get_koppel_history(session)
    unassigned = players[:]
    random.shuffle(unassigned)

    couples: list[Player] = []

    while unassigned:
        p1 = unassigned.pop(0)

        best_partner_idx = min(
            range(len(unassigned)),
            key=lambda i: history[tuple(sorted((p1.id, unassigned[i].id)))],
        )
        p2 = unassigned.pop(best_partner_idx)

        pair = tuple(sorted((p1.id, p2.id)))
        history[pair] += 1

        koppel_name = f"{p1.name} & {p2.name}"
        alt_name = f"{p2.name} & {p1.name}"

        koppel_player = session.scalars(
            select(Player).where((Player.name == koppel_name) | (Player.name == alt_name))
        ).first()

        if not koppel_player:
            koppel_player = Player(name=koppel_name, active=False)
            session.add(koppel_player)
            session.flush()

        couples.append(koppel_player)

    save_koppel_history(session, history)
    return couples


def placement_cost_entity(
    entity: Player,
    bucket: list[Player],
    history: dict[tuple[int, int], int],
    name_id_map: dict[str, int],
) -> int:
    """
    Berekent hoe 'duur' het is om een single of koppel in een poule te plaatsen.

    Bij koppels kijkt hij naar de individuele spelers binnen het koppel.
    Daardoor wordt voorkomen dat dezelfde darters steeds tegen elkaar in de poule komen.
    """
    entity_ids = entity_member_ids(entity, name_id_map)

    cost = 0
    for existing in bucket:
        existing_ids = entity_member_ids(existing, name_id_map)

        for id1 in entity_ids:
            for id2 in existing_ids:
                if id1 != id2:
                    cost += history[tuple(sorted((id1, id2)))]

    cost += random.uniform(0, 0.1 * len(bucket))
    return cost

# --- EINDE MYSTERIE KOPPEL LOGICA ---

def create_groups_for_evening(session: Session, evening: Evening, custom_sizes: list[int] = None, tournament_format: str = "single") -> list[Group]:
    present_players = [a.player for a in evening.attendances if a.present]
    
    # 🚀 CHECK TOERNOOIVORM
    if tournament_format == "koppel":
        if len(present_players) % 2 != 0:
            raise ValueError("Voor een koppeltoernooi moet er een exact EVEN aantal spelers aanwezig zijn.")
        if len(present_players) < 6:
            raise ValueError("Minimaal 6 spelers (3 koppels) nodig voor een toernooi.")
        
        # Laat het algoritme de duo's formeren
        entities_to_group = create_koppels(session, present_players)
    else:
        if len(present_players) < 3:
            raise ValueError("Minimaal 3 aanwezigen nodig voor een single toernooi")
        entities_to_group = present_players[:]

    reset_evening_groups(session, evening)
    history = individual_pair_history(session)
    name_id_map = build_player_name_id_map(session)

    # We gebruiken nu de 'entities_to_group' (Koppels of Singles) om de poules te bepalen
    target_sizes = custom_sizes if custom_sizes else choose_group_sizes(len(entities_to_group))
    
    groups = [Group(evening_id=evening.id, name=f"Poule {chr(65+i)}") for i in range(len(target_sizes))]
    session.add_all(groups)
    session.flush()

    buckets: list[list[Player]] = [[] for _ in target_sizes]
    unassigned = entities_to_group[:]
    random.shuffle(unassigned)

    while unassigned:
        player = unassigned.pop(0)
        best_idx = min(
    range(len(target_sizes)),
    key=lambda i: (
        placement_cost_entity(player, buckets[i], history, name_id_map)
        + (1000 if len(buckets[i]) >= target_sizes[i] else 0)
    ),
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
    grouped = grouped_rankings_for_evening(session, evening.id)
    group_names = sorted(grouped.keys())
    num_groups = len(group_names)

    # Totale platte ranking voor als we fallback nodig hebben
    flat_rankings = group_rankings_for_evening(session, evening.id)
    seed_players = [row[0] for row in flat_rankings]

    # Bepaal bracket size (4 of 8)
    bracket_size = 8 if len(seed_players) >= 8 else (4 if len(seed_players) >= 4 else 3)
    if len(seed_players) < 3:
        raise ValueError("Minimaal 3 spelers nodig voor knock-out")

    matches = []

    # -- SCENARIO 1: Precies 2 poules (De perfecte kruisfinale!) --
    if num_groups == 2 and bracket_size in (4, 8):
        g1_players = [row[0] for row in grouped[group_names[0]]]
        g2_players = [row[0] for row in grouped[group_names[1]]]

        # Check of elke poule genoeg spelers heeft
        players_per_group_needed = bracket_size // 2
        if len(g1_players) >= players_per_group_needed and len(g2_players) >= players_per_group_needed:
            if bracket_size == 4:
                # Halve Finale (4 spelers): 1A v 2B, 1B v 2A
                pairings = [
                    (g1_players[0], g2_players[1]), # Semi 1
                    (g2_players[0], g1_players[1])  # Semi 2
                ]
                phase = MatchPhase.SEMI
            else: 
                # Kwartfinale (8 spelers): 1A v 4B, 2B v 3A, 1B v 4A, 2A v 3B
                pairings = [
                    (g1_players[0], g2_players[3]), # QF 1
                    (g2_players[1], g1_players[2]), # QF 2 (Winnaar speelt tegen winnaar QF 1)
                    (g2_players[0], g1_players[3]), # QF 3
                    (g1_players[1], g2_players[2])  # QF 4 (Winnaar speelt tegen winnaar QF 3)
                ]
                phase = MatchPhase.QUARTER

            for idx, (p1, p2) in enumerate(pairings):
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

    # -- SCENARIO 2: 1 poule of 3+ poules (Geoptimaliseerde Seeding) --
    if num_groups > 2:
        # We zetten eerst alle nummers 1 op een rij, dán alle nummers 2, etc.
        seeded_list = []
        max_depth = max(len(grouped[g]) for g in group_names)
        for depth in range(max_depth):
            depth_players = []
            for g in group_names:
                if depth < len(grouped[g]):
                    depth_players.append(grouped[g][depth])
            # Sorteer de nummers 1 onderling op punten/legsaldo
            depth_players.sort(key=lambda x: (x[1], x[2]), reverse=True)
            seeded_list.extend([p[0] for p in depth_players])
        seed_players = seeded_list[:bracket_size]
    else:
        seed_players = seed_players[:bracket_size]

    # PDC Bracket logica: Zorgt dat Nummer 1 en Nummer 2 elkaar pas in de finale zien
    if bracket_size == 3:
        pair_indices = [(1, 2)]
        phase = MatchPhase.SEMI
    else:
        if bracket_size == 8:
            # 1v8 en 4v5 aan de linkerkant van het schema. 2v7 en 3v6 aan de rechterkant!
            pair_indices = [(0, 7), (3, 4), (1, 6), (2, 5)]
        else:
            pair_indices = [(0, 3), (1, 2)]
        phase = MatchPhase.SEMI if bracket_size == 4 else MatchPhase.QUARTER

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
        semis = sorted(by_phase[MatchPhase.SEMI], key=lambda x: x.bracket_order)
        if len(semis) == 1 and semis[0].winner_id:
            semi = semis[0]
            semi_players = {semi.player1_id, semi.player2_id}
            rankings = group_rankings_for_evening(session, evening.id)
            bye_player_id = next((player.id for player, _, _ in rankings if player.id not in semi_players), None)
            if bye_player_id and bye_player_id != semi.winner_id:
                session.add(
                    Match(
                        evening_id=evening.id,
                        phase=MatchPhase.FINAL,
                        player1_id=bye_player_id,
                        player2_id=semi.winner_id,
                        bracket_order=0,
                        board_number=1,
                    )
                )
        elif len(semis) >= 2 and all(m.winner_id for m in semis):
            winners = [m.winner_id for m in semis]
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

        def compare_rows(left: tuple[Player, int, int], right: tuple[Player, int, int]) -> int:
            lp, lpoints, llegs = left
            rp, rpoints, rlegs = right
            if lpoints != rpoints:
                return -1 if lpoints > rpoints else 1
            if llegs != rlegs:
                return -1 if llegs > rlegs else 1

            mutual = [m for m in group_matches if {m.player1_id, m.player2_id} == {lp.id, rp.id}]
            left_wins = sum(1 for m in mutual if m.winner_id == lp.id)
            right_wins = sum(1 for m in mutual if m.winner_id == rp.id)
            if left_wins != right_wins:
                return -1 if left_wins > right_wins else 1

            left_mutual_legs = 0
            right_mutual_legs = 0
            for m in mutual:
                if m.player1_id == lp.id:
                    left_mutual_legs += m.legs_player1
                    right_mutual_legs += m.legs_player2
                else:
                    left_mutual_legs += m.legs_player2
                    right_mutual_legs += m.legs_player1
            mutual_leg_diff = left_mutual_legs - right_mutual_legs
            if mutual_leg_diff != 0:
                return -1 if mutual_leg_diff > 0 else 1

            return -1 if lp.name.lower() < rp.name.lower() else 1 if lp.name.lower() > rp.name.lower() else 0

        rows.sort(key=cmp_to_key(compare_rows))
        grouped_rows[group.name] = rows

    return grouped_rows


def overall_standings(session: Session) -> list[StandingRow]:
    players = session.scalars(select(Player).where(Player.active.is_(True))).all()
    player_dict = {p.name.strip(): p for p in players}
    
    standings_data = {p.id: {"player": p, "points": 0, "leg_diff": 0, "attendance_count": 0, "wins": 0} for p in players}

    # Aanwezigheid
    attendances = session.scalars(select(Attendance).where(Attendance.present.is_(True))).all()
    for a in attendances:
        if a.player_id in standings_data:
            standings_data[a.player_id]["attendance_count"] += 1
            standings_data[a.player_id]["points"] += KNOCKOUT_POINTS["presence"]

    # Wedstrijden (Inclusief opsplitsen van Mysterie Koppels)
    matches = session.scalars(select(Match).options(joinedload(Match.player1), joinedload(Match.player2))).unique().all()
    for m in matches:
        if not m.player1 or not m.player2: continue
        
        # Splits de namen (werkt voor singles én koppels dankzij de ' & ')
        p1_names = [n.strip() for n in m.player1.name.split("&")]
        p2_names = [n.strip() for n in m.player2.name.split("&")]
        
        # Bereken de punten/legsaldo voor kant 1
        for name in p1_names:
            if name in player_dict:
                pid = player_dict[name].id
                standings_data[pid]["leg_diff"] += (m.legs_player1 - m.legs_player2)
                if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != m.player1_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS[m.phase]
                if m.phase == MatchPhase.FINAL and m.winner_id == m.player1_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS["winner"]
                    standings_data[pid]["wins"] += 1
        
        # Bereken de punten/legsaldo voor kant 2
        for name in p2_names:
            if name in player_dict:
                pid = player_dict[name].id
                standings_data[pid]["leg_diff"] += (m.legs_player2 - m.legs_player1)
                if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != m.player2_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS[m.phase]
                if m.phase == MatchPhase.FINAL and m.winner_id == m.player2_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS["winner"]
                    standings_data[pid]["wins"] += 1

    standings = [StandingRow(**data) for data in standings_data.values()]
    standings.sort(key=lambda x: (-x.points, -x.wins, -x.leg_diff, x.attendance_count, x.player.name.lower()))
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
    player_dict = {p.name.strip(): p for p in players}
    standings_data = {p.id: {"player": p, "points": 0, "leg_diff": 0} for p in players}
    
    attendances = session.scalars(
        select(Attendance).where(Attendance.evening_id.in_(evening_ids), Attendance.present.is_(True))
    ).all()
    for a in attendances:
        if a.player_id in standings_data:
            standings_data[a.player_id]["points"] += KNOCKOUT_POINTS["presence"]

    matches = session.scalars(select(Match).options(joinedload(Match.player1), joinedload(Match.player2)).where(Match.evening_id.in_(evening_ids))).unique().all()
    
    for m in matches:
        if not m.player1 or not m.player2: continue
        p1_names = [n.strip() for n in m.player1.name.split("&")]
        p2_names = [n.strip() for n in m.player2.name.split("&")]
        
        for name in p1_names:
            if name in player_dict:
                pid = player_dict[name].id
                standings_data[pid]["leg_diff"] += (m.legs_player1 - m.legs_player2)
                if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != m.player1_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS[m.phase]
                if m.phase == MatchPhase.FINAL and m.winner_id == m.player1_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS["winner"]
        
        for name in p2_names:
            if name in player_dict:
                pid = player_dict[name].id
                standings_data[pid]["leg_diff"] += (m.legs_player2 - m.legs_player1)
                if m.phase in (MatchPhase.QUARTER, MatchPhase.SEMI, MatchPhase.FINAL) and m.winner_id and m.winner_id != m.player2_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS[m.phase]
                if m.phase == MatchPhase.FINAL and m.winner_id == m.player2_id:
                    standings_data[pid]["points"] += KNOCKOUT_POINTS["winner"]
                    
    rows = [SeasonStandingRow(**data) for data in standings_data.values() if data["points"] > 0 or data["leg_diff"] != 0]
    rows.sort(key=lambda x: (x.points, x.leg_diff), reverse=True)
    return rows
