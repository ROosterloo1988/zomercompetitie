from zomercompetitie.services import (
    GROUP_MATCH_TEMPLATES,
    choose_group_sizes,
    create_groups_for_evening,
    create_knockout,
    grouped_rankings_for_evening,
    maybe_progress_knockout,
    overall_standings,
    parse_stat_values,
    serialize_stat_values,
    validate_evening_groups,
)


def test_choose_group_sizes_for_12():
    sizes = choose_group_sizes(12)
    assert sum(sizes) == 12
    assert all(s in {3, 4, 5, 6} for s in sizes)


def test_choose_group_sizes_for_9():
    sizes = choose_group_sizes(9)
    assert sum(sizes) == 9


def test_group_match_templates_have_expected_match_counts():
    assert len(GROUP_MATCH_TEMPLATES[3]) == 6
    assert len(GROUP_MATCH_TEMPLATES[4]) == 6
    assert len(GROUP_MATCH_TEMPLATES[5]) == 10
    assert len(GROUP_MATCH_TEMPLATES[6]) == 15


def test_group_match_templates_have_unique_pairs_except_size_3_double_round():
    for size in (4, 5, 6):
        pairs = {tuple(sorted(p)) for p in GROUP_MATCH_TEMPLATES[size]}
        expected = size * (size - 1) // 2
        assert len(pairs) == expected

    pairs3 = [tuple(sorted(p)) for p in GROUP_MATCH_TEMPLATES[3]]
    assert len(pairs3) == 6
    assert len(set(pairs3)) == 3


def test_parse_stat_values_filters_and_parses():
    assert parse_stat_values("140, 167, 99", minimum=100) == [140, 167]
    assert parse_stat_values("11,13,15,16", minimum=1, maximum=15) == [11, 13, 15]


def test_serialize_stat_values_roundtrip():
    values = [140, 167, 120]
    assert parse_stat_values(serialize_stat_values(values), minimum=100) == values

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from zomercompetitie.db import Base
from zomercompetitie.models import Attendance, Evening, EveningStatus, Group, GroupAssignment, Match, MatchPhase, Player, Season, SeasonEvening, SeasonStatus
from zomercompetitie.services import evening_lock_state


def _session_for_test():
    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return TestingSession()


def test_evening_lock_state_locks_closed_evening():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 6, 1), status=EveningStatus.CLOSED)
    session.add(evening)
    session.commit()

    locked, reason = evening_lock_state(session, evening)
    assert locked is True
    assert "alleen-lezen" in (reason or "")



def test_evening_lock_state_locks_evening_in_closed_season():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 6, 8))
    season = Season(name="Seizoen 2026", status=SeasonStatus.CLOSED)
    session.add_all([evening, season])
    session.flush()
    session.add(SeasonEvening(season_id=season.id, evening_id=evening.id))
    session.commit()

    locked, reason = evening_lock_state(session, evening)
    assert locked is True
    assert "gearchiveerd" in (reason or "")



def test_evening_lock_state_open_evening_in_open_season_is_editable():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 6, 15), status=EveningStatus.DRAFT)
    season = Season(name="Seizoen 2027", status=SeasonStatus.OPEN)
    session.add_all([evening, season])
    session.flush()
    session.add(SeasonEvening(season_id=season.id, evening_id=evening.id))
    session.commit()

    locked, reason = evening_lock_state(session, evening)
    assert locked is False
    assert reason is None


def test_choose_group_sizes_balanced_examples():
    assert choose_group_sizes(4) == [4]
    assert choose_group_sizes(5) == [5]
    assert choose_group_sizes(6) == [6]
    assert choose_group_sizes(7) == [4, 3]
    assert choose_group_sizes(8) == [4, 4]
    assert choose_group_sizes(11) == [6, 5]


def test_create_groups_for_evening_resets_old_groups_and_matches():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 6, 22))
    players = [Player(name=f"Speler {idx}") for idx in range(4)]
    session.add(evening)
    session.add_all(players)
    session.flush()
    for player in players:
        session.add(Attendance(evening_id=evening.id, player_id=player.id, present=True))
    session.commit()

    create_groups_for_evening(session, evening)
    session.commit()
    assert len(evening.groups) == 1
    first_group_ids = {group.id for group in evening.groups}
    first_match_ids = {match.id for match in evening.matches}

    create_groups_for_evening(session, evening)
    session.commit()
    session.refresh(evening)

    assert len(evening.groups) == 1
    assert len(evening.matches) == 6
    assert len({group.id for group in evening.groups}) == 1
    assert len({match.id for match in evening.matches}) == 6


def test_validate_evening_groups_rejects_invalid_group_sizes():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 6, 29))
    players = [Player(name=f"Invalid {idx}") for idx in range(2)]
    session.add(evening)
    session.add_all(players)
    session.flush()

    group = Group(evening_id=evening.id, name="Poule A")
    session.add(group)
    session.flush()
    for player in players:
        session.add(GroupAssignment(group_id=group.id, player_id=player.id))
    session.commit()

    try:
        validate_evening_groups(session, evening)
        assert False, "Expected invalid groups to raise"
    except ValueError as exc:
        assert "Poules zijn ongeldig" in str(exc)


def test_create_knockout_with_7_players_starts_at_semi():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 7, 6))
    players = [Player(name=f"Speler {idx}") for idx in range(7)]
    session.add(evening)
    session.add_all(players)
    session.flush()

    for player in players:
        session.add(Attendance(evening_id=evening.id, player_id=player.id, present=True))
    session.commit()

    create_groups_for_evening(session, evening)
    session.commit()
    session.refresh(evening)
    knockout_matches = create_knockout(session, evening)
    session.commit()

    assert len(knockout_matches) == 2
    assert all(match.phase == MatchPhase.SEMI for match in knockout_matches)


def test_create_knockout_with_3_players_creates_single_semi():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 7, 13))
    players = [Player(name=f"Mini {idx}") for idx in range(3)]
    session.add(evening)
    session.add_all(players)
    session.flush()
    for player in players:
        session.add(Attendance(evening_id=evening.id, player_id=player.id, present=True))
    session.commit()

    create_groups_for_evening(session, evening)
    session.commit()
    knockout_matches = create_knockout(session, evening)
    session.commit()

    assert len(knockout_matches) == 1
    assert knockout_matches[0].phase == MatchPhase.SEMI


def test_3_player_knockout_progresses_to_final_with_bye():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 7, 20))
    players = [Player(name=f"Bye {idx}") for idx in range(3)]
    session.add(evening)
    session.add_all(players)
    session.flush()
    for player in players:
        session.add(Attendance(evening_id=evening.id, player_id=player.id, present=True))
    session.commit()

    create_groups_for_evening(session, evening)
    session.commit()
    knockout_matches = create_knockout(session, evening)
    session.commit()

    semi = knockout_matches[0]
    semi.legs_player1 = 3
    semi.legs_player2 = 1
    semi.winner_id = semi.player1_id
    maybe_progress_knockout(session, evening)
    session.commit()

    finals = session.query(Match).filter(Match.evening_id == evening.id, Match.phase == MatchPhase.FINAL).all()
    assert len(finals) == 1
    final = finals[0]
    assert final.player1_id != final.player2_id
    assert semi.winner_id in {final.player1_id, final.player2_id}


def test_group_ranking_uses_head_to_head_on_equal_points_and_legs():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 7, 27))
    zulu = Player(name="Zulu")
    alpha = Player(name="Alpha")
    charlie = Player(name="Charlie")
    delta = Player(name="Delta")
    session.add_all([evening, zulu, alpha, charlie, delta])
    session.flush()

    group = Group(evening_id=evening.id, name="Poule A")
    session.add(group)
    session.flush()
    for player in (zulu, alpha, charlie, delta):
        session.add(GroupAssignment(group_id=group.id, player_id=player.id))

    def add_match(p1: Player, p2: Player, l1: int, l2: int):
        session.add(
            Match(
                evening_id=evening.id,
                phase=MatchPhase.GROUP,
                group_id=group.id,
                player1_id=p1.id,
                player2_id=p2.id,
                legs_player1=l1,
                legs_player2=l2,
                winner_id=p1.id if l1 > l2 else p2.id if l2 > l1 else None,
            )
        )

    # Zulu and Alpha end on equal points and equal legsaldo; Zulu won head-to-head.
    add_match(zulu, alpha, 3, 0)
    add_match(zulu, charlie, 0, 3)
    add_match(zulu, delta, 3, 0)
    add_match(alpha, charlie, 3, 0)
    add_match(alpha, delta, 3, 0)
    add_match(charlie, delta, 3, 0)
    session.commit()

    standings = grouped_rankings_for_evening(session, evening.id)["Poule A"]
    assert standings[0][0].id == zulu.id
    assert standings[1][0].id == alpha.id


def test_overall_standing_prefers_fewer_attendances_on_equal_points():
    session = _session_for_test()
    evening1 = Evening(event_date=date(2026, 8, 3))
    evening2 = Evening(event_date=date(2026, 8, 10))
    evening3 = Evening(event_date=date(2026, 8, 17))
    p1 = Player(name="Efficient")
    p2 = Player(name="Frequent")
    opponent = Player(name="Opponent")
    session.add_all([evening1, evening2, evening3, p1, p2, opponent])
    session.flush()

    session.add(Attendance(evening_id=evening1.id, player_id=p1.id, present=True))
    session.add(Attendance(evening_id=evening1.id, player_id=p2.id, present=True))
    session.add(Attendance(evening_id=evening2.id, player_id=p2.id, present=True))
    session.commit()

    # p1 gets +2 KO loser points so both players end up equal in total points.
    session.add(
        Match(
            evening_id=evening3.id,
            phase=MatchPhase.QUARTER,
            player1_id=p1.id,
            player2_id=opponent.id,
            legs_player1=1,
            legs_player2=3,
            winner_id=opponent.id,
        )
    )
    session.commit()

    rows = overall_standings(session)
    efficient = next(row for row in rows if row.player.id == p1.id)
    frequent = next(row for row in rows if row.player.id == p2.id)
    assert efficient.points == frequent.points
    # With equal points, fewer attendances should rank higher.
    assert efficient.attendance_count < frequent.attendance_count
    assert rows[0].player.id == p1.id


# ---------------------------------------------------------------------------
# Schrijvers (scorekeepers)
# ---------------------------------------------------------------------------
from zomercompetitie.services import WRITER_TEMPLATES, match_loser_id


class _FakeMatch:
    def __init__(self, player1_id, player2_id, winner_id):
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.winner_id = winner_id


def test_writer_templates_match_group_template_lengths():
    for size in (3, 4, 5, 6):
        assert len(WRITER_TEMPLATES[size]) == len(GROUP_MATCH_TEMPLATES[size])


def test_writer_is_never_a_player_in_the_match():
    # De schrijver mag nooit een van de twee spelers van de wedstrijd zijn.
    for size in (3, 4, 5, 6):
        for (a, b), writer in zip(GROUP_MATCH_TEMPLATES[size], WRITER_TEMPLATES[size]):
            assert writer not in (a, b)


def test_writers_are_balanced_within_one():
    # Iedereen schrijft ongeveer even vaak: verschil hooguit 1.
    for size in (3, 4, 5, 6):
        counts = [0] * size
        for writer in WRITER_TEMPLATES[size]:
            counts[writer] += 1
        assert max(counts) - min(counts) <= 1


def test_writers_never_two_matches_in_a_row():
    # Niemand schrijft twee wedstrijden achter elkaar.
    for size in (3, 4, 5, 6):
        writers = WRITER_TEMPLATES[size]
        assert all(writers[i] != writers[i - 1] for i in range(1, len(writers)))


def test_writers_stay_fair_over_time():
    # Temporele eerlijkheid: niemand schrijft een tweede keer voordat iedereen
    # één keer heeft geschreven. De LOPENDE telling blijft altijd binnen 1.
    for size in (3, 4, 5, 6):
        counts = [0] * size
        for writer in WRITER_TEMPLATES[size]:
            counts[writer] += 1
            assert max(counts) - min(counts) <= 1, f"poulegrootte {size}: lopende verdeling scheef"


def test_match_loser_id():
    assert match_loser_id(_FakeMatch(1, 2, 1)) == 2
    assert match_loser_id(_FakeMatch(1, 2, 2)) == 1
    assert match_loser_id(_FakeMatch(1, 2, None)) is None


# ---------------------------------------------------------------------------
# Optimistic locking (versiecheck) op uitslagen
# ---------------------------------------------------------------------------
import pytest
from zomercompetitie.services import save_match_result, StaleMatchError


def _evening_with_match():
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 7, 1))
    p1 = Player(name="Speler A")
    p2 = Player(name="Speler B")
    session.add_all([evening, p1, p2])
    session.flush()
    match = Match(
        evening_id=evening.id,
        phase=MatchPhase.GROUP,
        player1_id=p1.id,
        player2_id=p2.id,
    )
    session.add(match)
    session.commit()
    return session, match


def test_save_match_result_bumps_row_version():
    session, match = _evening_with_match()
    assert match.row_version == 0
    save_match_result(session, match.id, 3, 1)
    assert match.row_version == 1
    save_match_result(session, match.id, 3, 2)
    assert match.row_version == 2


def test_save_match_result_accepts_matching_version():
    session, match = _evening_with_match()
    save_match_result(session, match.id, 3, 1, expected_version=0)
    assert match.legs_player1 == 3 and match.legs_player2 == 1
    assert match.winner_id == match.player1_id
    assert match.row_version == 1


def test_save_match_result_rejects_stale_version():
    session, match = _evening_with_match()
    # Iemand anders slaat eerst op -> versie wordt 1
    save_match_result(session, match.id, 3, 0)
    # Verouderd toestel denkt nog dat de versie 0 is -> moet weigeren
    with pytest.raises(StaleMatchError):
        save_match_result(session, match.id, 1, 3, expected_version=0)
    # De eerdere uitslag blijft staan (niet overschreven)
    assert match.legs_player1 == 3 and match.legs_player2 == 0


def test_save_match_result_none_version_skips_check():
    session, match = _evening_with_match()
    save_match_result(session, match.id, 2, 1)
    # expected_version=None => geen check, gewoon opslaan
    save_match_result(session, match.id, 3, 2, expected_version=None)
    assert match.legs_player1 == 3 and match.row_version == 2


# ---------------------------------------------------------------------------
# Dynamische schrijver-toewijzing: poulewedstrijden
# ---------------------------------------------------------------------------
from zomercompetitie.services import assign_pending_group_scorekeepers, assign_knockout_scorekeepers


def _evening_with_groups(n_players: int = 4):
    """Maakt een avond met één poule van n_players spelers aan."""
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 8, 1))
    session.add(evening)
    session.flush()

    players = [Player(name=f"Speler {i+1}") for i in range(n_players)]
    session.add_all(players)
    session.flush()

    for p in players:
        session.add(Attendance(evening_id=evening.id, player_id=p.id, present=True))

    group = Group(evening_id=evening.id, name="Poule A")
    session.add(group)
    session.flush()

    for p in players:
        session.add(GroupAssignment(group_id=group.id, player_id=p.id))
    session.flush()

    from zomercompetitie.services import create_group_matches
    create_group_matches(session, evening.id, group, players, board_count=1)
    session.flush()

    return session, evening, players


def test_assign_pending_group_scorekeepers_schrijver_speelt_nooit_mee():
    session, evening, players = _evening_with_groups(4)
    assign_pending_group_scorekeepers(session, evening)
    matches = session.scalars(select(Match).where(Match.evening_id == evening.id)).all()
    for m in matches:
        assert m.scorekeeper_id not in {m.player1_id, m.player2_id}, (
            f"Schrijver {m.scorekeeper_id} speelt zelf mee in wedstrijd {m.id}"
        )


def test_assign_pending_group_scorekeepers_iedereen_schrijft_gelijk():
    session, evening, players = _evening_with_groups(4)
    assign_pending_group_scorekeepers(session, evening)
    matches = session.scalars(select(Match).where(Match.evening_id == evening.id)).all()
    from collections import Counter
    counts = Counter(m.scorekeeper_id for m in matches if m.scorekeeper_id is not None)
    vals = list(counts.values())
    assert vals, "Geen schrijvers toegewezen"
    assert max(vals) - min(vals) <= 1, f"Schrijfbeurten niet eerlijk verdeeld: {counts}"


def test_assign_pending_group_scorekeepers_gespeelde_match_behoudt_schrijver():
    session, evening, players = _evening_with_groups(4)
    matches = session.scalars(
        select(Match).where(Match.evening_id == evening.id).order_by(Match.bracket_order)
    ).all()
    first = matches[0]
    first.legs_player1 = 3
    first.legs_player2 = 1
    first.winner_id = first.player1_id
    original_writer = first.scorekeeper_id
    session.flush()

    # Forceer een andere schrijver om te controleren dat die NIET overschreven wordt
    assign_pending_group_scorekeepers(session, evening)
    assert first.scorekeeper_id == original_writer, "Gespeelde wedstrijd mag schrijver niet verliezen"


def test_assign_pending_group_scorekeepers_grote_poule():
    """Poule van 6: alle constraints moeten ook gelden na dynamische toewijzing."""
    session, evening, players = _evening_with_groups(6)
    assign_pending_group_scorekeepers(session, evening)
    matches = session.scalars(select(Match).where(Match.evening_id == evening.id)).all()
    for m in matches:
        assert m.scorekeeper_id not in {m.player1_id, m.player2_id}


# ---------------------------------------------------------------------------
# Dynamische schrijver-toewijzing: knock-outwedstrijden
# ---------------------------------------------------------------------------

def _evening_with_knockout(n_players: int = 4):
    """Maakt een avond met knock-outwedstrijden (zonder poules, direct ingevoerd)."""
    session = _session_for_test()
    evening = Evening(event_date=date(2026, 8, 2))
    session.add(evening)
    players = [Player(name=f"KO {i+1}") for i in range(n_players)]
    session.add_all(players)
    session.flush()

    group = Group(evening_id=evening.id, name="Poule A")
    session.add(group)
    session.flush()
    for p in players:
        session.add(GroupAssignment(group_id=group.id, player_id=p.id))
    session.flush()

    # 4-spelers: 2 halve finales + 1 finale
    m1 = Match(evening_id=evening.id, phase=MatchPhase.SEMI, bracket_order=0,
               player1_id=players[0].id, player2_id=players[1].id)
    m2 = Match(evening_id=evening.id, phase=MatchPhase.SEMI, bracket_order=1,
               player1_id=players[2].id, player2_id=players[3].id)
    mf = Match(evening_id=evening.id, phase=MatchPhase.FINAL, bracket_order=0,
               player1_id=players[0].id, player2_id=players[2].id)
    session.add_all([m1, m2, mf])
    session.flush()
    return session, evening, players, [m1, m2, mf]


def test_assign_knockout_scorekeepers_schrijver_speelt_nooit_mee():
    session, evening, players, matches = _evening_with_knockout()
    assign_knockout_scorekeepers(session, evening)
    for m in matches:
        if m.scorekeeper_id is not None:
            assert m.scorekeeper_id not in {m.player1_id, m.player2_id}, (
                f"KO schrijver {m.scorekeeper_id} speelt zelf mee in wedstrijd {m.id}"
            )


def test_assign_knockout_scorekeepers_finale_schrijver_is_verliezer_semi():
    session, evening, players, matches = _evening_with_knockout()
    semi1, semi2, final = matches
    # Speel halve finales: speler 0 wint semi1, speler 2 wint semi2
    semi1.legs_player1 = 3; semi1.legs_player2 = 1; semi1.winner_id = players[0].id
    semi2.legs_player1 = 3; semi2.legs_player2 = 0; semi2.winner_id = players[2].id
    session.flush()

    assign_knockout_scorekeepers(session, evening)
    # Verliezer van semi2 (players[3]) schrijft de finale
    assert final.scorekeeper_id == players[3].id


def test_assign_knockout_scorekeepers_finale_writer_none_als_semi_niet_gespeeld():
    session, evening, players, matches = _evening_with_knockout()
    semi1, semi2, final = matches
    # Semi's nog niet gespeeld → finale kan nog geen schrijver hebben
    assign_knockout_scorekeepers(session, evening)
    # Finale schrijver is None want er is nog geen verliezer bekend
    assert final.scorekeeper_id is None


def test_assign_knockout_scorekeepers_gespeelde_ko_behoudt_schrijver():
    session, evening, players, matches = _evening_with_knockout()
    semi1, semi2, final = matches
    semi1.legs_player1 = 3; semi1.legs_player2 = 0; semi1.winner_id = players[0].id
    semi1.scorekeeper_id = players[2].id  # handmatig gezet
    session.flush()

    assign_knockout_scorekeepers(session, evening)
    assert semi1.scorekeeper_id == players[2].id, "Gespeelde KO-match mag schrijver niet verliezen"
