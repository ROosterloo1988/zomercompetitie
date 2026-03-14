from zomercompetitie.services import GROUP_MATCH_TEMPLATES, choose_group_sizes, parse_stat_values, serialize_stat_values


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
from zomercompetitie.models import Evening, EveningStatus, Season, SeasonEvening, SeasonStatus
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
