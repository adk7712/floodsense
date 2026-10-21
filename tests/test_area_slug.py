import pytest

from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS, area_from_slug


@pytest.mark.parametrize(
    "slug",
    [
        "bukit-merah",
        "bukit merah",
        "Bukit_Merah",
        "BUKIT-MERAH",
        " bukit  merah ",
        "bukit__merah",
        "bukit_ merah",
    ],
)
def test_bukit_merah_spellings_resolve(slug):
    assert area_from_slug(slug) == "BUKIT MERAH"


def test_unknown_slug_is_none():
    assert area_from_slug("atlantis") is None
    assert area_from_slug("bukit-merah-x") is None


@pytest.mark.parametrize("slug", ["", "   ", "_", None, 42])
def test_empty_or_non_string_is_none(slug):
    assert area_from_slug(slug) is None


@pytest.mark.parametrize("area", URA_PLANNING_AREAS)
def test_every_ura_area_round_trips(area):
    hyphen = area.lower().replace(" ", "-")
    assert area_from_slug(hyphen) == area
    assert area_from_slug(area.lower()) == area
    assert area_from_slug(area) == area
    assert area_from_slug(hyphen.replace("-", " ")) == area
    assert area_from_slug(hyphen.replace("-", "_").upper()) == area
