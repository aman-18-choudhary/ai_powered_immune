from scam_contracts.geo import CITY_CODES, STATE_CODES

from geo_intel import table


def test_table_and_contract_enums_list_the_same_codes():
    assert set(table.BY_CODE) == set(CITY_CODES) and len(table.CITIES) == len(CITY_CODES)
    assert {table.STATE_CODE[c[1]] for c in table.CITIES} == set(STATE_CODES)
    assert set(table.STATE_CODE.values()) == set(STATE_CODES)


def test_rows_are_plausible_and_unique():
    assert len({c[0] for c in table.CITIES}) == len(table.CITIES)
    for city, state, lat, lon, w in table.CITIES:
        assert 6 < lat < 37 and 68 < lon < 98 and w > 0 and state in table.STATE_CODE
        assert round(lat, 3) == lat and round(lon, 3) == lon  # no finer than ~100 m
        assert table.BY_CODE[table.CITY_CODE[city]][0] == city


def test_state_anchor_is_the_largest_city():
    assert table.STATE_ANCHOR["MH"][0] == "Mumbai"
    assert table.STATE_ANCHOR["TN"][0] == "Chennai"
    assert table.STATE_ANCHOR["DL"][0] == "Delhi"


def test_city_for_is_deterministic_and_total():
    assert table.city_for("Karnataka", "Bengaluru Urban", None)[0] == "Bengaluru"
    assert table.city_for("Atlantis", "Nowhere", None) == table.FALLBACK
    assert table.city_for(None, None, None)[0] == "Delhi"
    assert table.nearest_city(22.56, 88.34)[0] == "Kolkata"
