from src.plugins.essential.dock_taskbar._region import region_rect


def test_whole_pixels_are_kept():
    assert region_rect((10, 4, 200, 56), (400, 64)) == (10, 4, 200, 56)


def test_fractions_round_outwards():
    assert region_rect((10.4, 3.6, 100.2, 50.0), (400, 64)) == (10, 3, 101, 51)


def test_never_leaves_the_window():
    assert region_rect((-5, -2, 500, 80), (400, 64)) == (0, 0, 400, 64)


def test_nothing_inside_is_none():
    assert region_rect((500, 0, 40, 40), (400, 64)) is None
    assert region_rect((0, 0, 0, 40), (400, 64)) is None
    assert region_rect((0, 0, 40, 40), (0, 0)) is None
