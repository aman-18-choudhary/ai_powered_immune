from demo_server import geo


def test_city_for_is_deterministic_and_total():
    assert geo.city_for("Gujarat", "Ahmedabad", "Ahmedabad")[0] == "Ahmedabad"
    # district alias, state fallback, unknown fallback: never raises
    assert geo.city_for("Karnataka", "Bengaluru Urban", None)[0] == "Bengaluru"
    assert geo.city_for("Tamil Nadu", None, None)[0] == "Chennai"  # largest city of the state
    assert geo.city_for("Atlantis", "Nowhere", None)[0] == "Delhi"
    assert geo.city_for(None, None, None)[0] == "Delhi"
    assert geo.city_for("tamil nadu", " CHENNAI ", None) == geo.city_for("Tamil Nadu", "Chennai")


def test_table_is_well_formed_and_in_india():
    seen = set()
    for city, state, lat, lon, w in geo.CITIES:
        assert city not in seen and state and w > 0
        seen.add(city)
        assert 6.0 < lat < 37.0 and 68.0 < lon < 98.0, city


def test_nearest_city():
    assert geo.nearest_city(22.56, 88.34)[0] == "Kolkata"
    assert geo.nearest_city(19.0, 72.8)[0] == "Mumbai"


def test_hotspots_windows_and_trend():
    row = geo.city_for("Gujarat", "Ahmedabad")
    kol = geo.city_for("West Bengal", "Kolkata")
    pts = [
        geo.point(100.0, "scam_call", row),  # previous window (600 s) for now_t = 1000
        geo.point(500.0, "held_transfer", row),
        geo.point(800.0, "held_transfer", row),
        geo.point(900.0, "held_transfer", row),
        geo.point(950.0, "protected", kol),
        geo.point(960.0, "cash_out", kol),
    ]
    out = geo.hotspots(pts, 1000.0, window_s=600.0)
    cells = {c["city"]: c for c in out["hotspots"]}
    a = cells["Ahmedabad"]
    assert a["count"] == 3 and a["prev_count"] == 1 and a["total"] == 4 and a["trend"] == "up"
    assert cells["Kolkata"]["count"] == 1 and "cash_out" not in cells["Kolkata"]["kinds"]
    assert [c["city"] for c in out["cash_out_points"]] == ["Kolkata"]
    # same input, same output
    assert geo.hotspots(pts, 1000.0, window_s=600.0) == out
    down = geo.hotspots(pts[:1], 1000.0)
    assert down["hotspots"][0]["trend"] == "down"


def test_table_is_the_shared_geo_intel_table():
    from geo_intel import table

    assert geo.CITIES is table.CITIES
    assert geo.city_for is table.city_for and geo.nearest_city is table.nearest_city
