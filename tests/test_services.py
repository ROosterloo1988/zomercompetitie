from zomercompetitie.services import choose_group_sizes


def test_choose_group_sizes_for_12():
    sizes = choose_group_sizes(12)
    assert sum(sizes) == 12
    assert all(s in {3, 4, 5, 6} for s in sizes)


def test_choose_group_sizes_for_9():
    sizes = choose_group_sizes(9)
    assert sum(sizes) == 9
