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

from sqlalchemy import create_engine
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
