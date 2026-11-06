"""Music and volume without the mouse: volume words, and finding the video on YouTube's page."""

import pytest

from blip import media


@pytest.mark.parametrize(
    ("said", "now", "level"),
    [
        ("30", 0.6, 0.30),
        ("30%", 0.6, 0.30),
        ("-15", 0.6, 0.45),
        ("+10", 0.95, 1.0),
        ("a bit lower", 0.5, 0.38),
        ("lower", 0.5, 0.30),
        ("quieter", 0.1, 0.05),
        ("louder", 0.5, 0.70),
        ("low", 0.9, 0.2),
        ("mute", 0.4, 0.0),
    ],
)
def test_volume_words(said, now, level):
    assert media.parse_volume(said, now) == pytest.approx(level)


def test_volume_nonsense_is_refused():
    assert media.parse_volume("purple", 0.5) is None


async def test_the_top_video_and_its_title_come_from_the_results_page(monkeypatch):
    page = (
        'junk "videoRenderer":{"videoId":"Zi_XLOBDo_Y","thumbnail":{},"title":{"runs":[{"text":'
        '"Michael Jackson - Billie Jean (Official Video)"}]}} more "videoRenderer":{"videoId":"tSi6Dn1H36Y"'
    )

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None, headers=None):
            assert params == {"search_query": "billie jean"}
            return type("R", (), {"text": page})()

    monkeypatch.setattr(media.httpx, "AsyncClient", Fake)
    assert await media.find_video("billie jean") == (
        "Zi_XLOBDo_Y",
        "Michael Jackson - Billie Jean (Official Video)",
    )
