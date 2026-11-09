from pyxi import localzone, weather


def test_windows_zone_names_become_a_city(monkeypatch):
    monkeypatch.setattr(localzone, "windows_zone", lambda: "British Columbia Standard Time")
    assert localzone.local_zone() == "America/Vancouver"
    assert weather.city_of(localzone.local_zone()) == "Vancouver"
    monkeypatch.setattr(localzone, "windows_zone", lambda: "Bangladesh Standard Time")
    assert weather.city_of(localzone.local_zone()) == "Dhaka"
