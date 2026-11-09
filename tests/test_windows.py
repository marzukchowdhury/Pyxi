"""Window snapping: halves of the work area, with no gap between two snapped windows."""

import pytest

from pyxi import windows


@pytest.fixture
def placed(monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "work_area", lambda hwnd: (0, 0, 1921, 1032))
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: calls.append(rect))
    return calls


def test_left_and_right_halves_meet_exactly(placed):
    windows.snap(1, "left")
    windows.snap(2, "right")
    left, right = placed
    assert left == (0, 0, 960, 1032)
    assert right == (960, 0, 961, 1032)  # an odd pixel goes to the right half, no gap
    assert left[0] + left[2] == right[0]


def test_top_and_bottom(placed):
    windows.snap(1, "top")
    windows.snap(2, "bottom")
    top, bottom = placed
    assert top[1] + top[3] == bottom[1]
    assert top[3] + bottom[3] == 1032


def test_browser_flags_cover_the_common_browsers():
    for exe in ("firefox.exe", "chrome.exe", "msedge.exe", "brave.exe"):
        assert windows.BROWSER_FLAGS[exe].endswith("new-window")


def test_named_window_finds_the_app_the_request_names():
    from pyxi.windows import named_window

    open_now = [
        (11, "Pyxi overlay", r"C:\x\electron.exe"),
        (12, "Claude", r"C:\x\claude.exe"),
        (13, "*notes - Notepad", r"C:\Windows\notepad.exe"),
        (14, "Vercel - Google Chrome", r"C:\x\chrome.exe"),
        (15, "main.py - wisp - Visual Studio Code", r"C:\x\Code.exe"),
    ]
    assert named_window("Guide me to turn on word wrap in Notepad?", open_now) == 13
    assert named_window("walk me through this in google chrome", open_now) == 14
    assert named_window("show me how to use visual studio code", open_now) == 15
    # Ordinary words that happen to be program names don't count.
    assert named_window("explain this code to me", open_now) is None
    assert named_window("what's on my screen?", open_now) is None


def test_panes_tile_the_screen_without_gaps():
    from pyxi.windows import pane_rects

    area = (0, 0, 1921, 1032)
    three = pane_rects(area, 3)
    assert [r[0] for r in three] == [0, 640, 1280] and sum(r[2] for r in three) == 1921
    assert all(r[3] == 1032 for r in three)
    grid = pane_rects(area, 4)
    assert grid[3] == (960, 516, 961, 516)
    main = pane_rects(area, 3, "main")
    assert main[0] == (0, 0, 960, 1032) and main[1][1] == 0 and main[2][1] == 516


def test_open_windows_are_found_by_site_or_name():
    from pyxi.windows import window_like

    open_now = [
        (1, "Facebook - Mozilla Firefox", r"C:\x\firefox.exe"),
        (2, "Inbox (3) - me@gmail.com - Gmail - Mozilla Firefox", r"C:\x\firefox.exe"),
        (3, "Spotify Premium", r"C:\x\Spotify.exe"),
    ]
    assert window_like("https://mail.google.com", open_now, set()) == 2
    assert window_like("Gmail", open_now, set()) == 2
    assert window_like("spotify", open_now, set()) == 3
    assert window_like("https://stake.com", open_now, set()) is None
    assert window_like("facebook.com", open_now, {1}) is None  # already placed


async def test_three_parts_in_one_call(settings, monkeypatch):
    """ "Split my screen in three: Facebook (here), Stake and Gmail": one tool call, no hands."""
    from test_brain_and_server import make_brain

    placed, opened = {}, []
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "work_area", lambda hwnd: (0, 0, 1920, 1032))
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: placed.__setitem__(hwnd, rect))
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [
            (7, "Facebook - Mozilla Firefox", "firefox.exe"),
            (8, "Inbox - Gmail - Mozilla Firefox", "firefox.exe"),
        ],
    )

    async def fake_open(target, like=None, is_url=True):
        opened.append(target)
        return 9

    monkeypatch.setattr(windows, "open_new_window", fake_open)
    monkeypatch.setattr("pyxi.companion.brain.asyncio.sleep", _no_sleep)
    brain, _ = make_brain(settings)
    brain._ctx = type("C", (), {"window": type("W", (), {"hwnd": 7})()})()
    note = await brain._arrange_windows(
        {"layout": "panes", "panes": ["current", "https://stake.com", "Gmail"]}
    )
    assert note is None and opened == ["https://stake.com"]  # Gmail was already open: reused
    assert placed == {7: (0, 0, 640, 1032), 9: (640, 0, 640, 1032), 8: (1280, 0, 640, 1032)}



async def test_panes_end_up_in_front_of_the_window_they_were_in(settings, monkeypatch):
    """Live: "Facebook on the left, YouTube on the right" from a full-screen New Tab put both
    panes behind it, because the New Tab got the focus back at the end."""
    from test_brain_and_server import make_brain

    focused: list[int] = []
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "work_area", lambda hwnd: (0, 0, 1920, 1032))
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: None)
    monkeypatch.setattr(windows, "focus", lambda hwnd: focused.append(hwnd) or True)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [
            (7, "Mozilla Firefox", "firefox.exe"),  # the New Tab they were in
            (8, "(1) Facebook - Mozilla Firefox", "firefox.exe"),
            (9, "Music - YouTube - Mozilla Firefox", "firefox.exe"),
        ],
    )
    monkeypatch.setattr("pyxi.companion.brain.asyncio.sleep", _no_sleep)
    brain, _ = make_brain(settings)
    brain._ctx = type("C", (), {"window": type("W", (), {"hwnd": 7})()})()
    await brain._arrange_windows({"layout": "panes", "arrangement": "columns", "panes": ["Facebook", "YouTube"]})
    assert 7 not in focused and set(focused) == {8, 9}


async def _no_sleep(_s):
    return None


THREE = [
    {"n": 1, "name": "left", "rect": (0, 0, 1920, 1080), "work": (0, 0, 1920, 1032), "primary": True},
    {"n": 2, "name": "middle", "rect": (1920, 0, 1080, 1920), "work": (1920, 0, 1080, 1872), "primary": False},
    {"n": 3, "name": "right", "rect": (3000, 0, 1080, 1920), "work": (3000, 0, 1080, 1872), "primary": False},
]


def test_every_screen_and_whats_on_it(monkeypatch):
    """'Can you see I'm using three screens?' got 'I can only see one screen'."""
    monkeypatch.setattr(windows, "screens", lambda: THREE)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [
            (1, "Pyxi overlay", ""),
            (2, "(1) Facebook - Mozilla Firefox", ""),
            (3, "Music - YouTube - Mozilla Firefox", ""),
            (4, "Claude", ""),
        ],
    )
    rects = {1: (0, 0, 1920, 1032), 2: (0, 0, 960, 1032), 3: (1912, -8, 1096, 1888), 4: (2992, -8, 1096, 1888)}
    monkeypatch.setattr(windows, "rect_of", lambda h: rects[h])
    text = windows.screens_summary(mouse=(3500, 900))
    assert "3 screens" in text and "Pyxi overlay" not in text
    assert "- Left screen: (1) Facebook" in text
    assert "- Middle screen: Music - YouTube" in text
    assert "- Right screen (the screenshot is of this one" in text
    monkeypatch.setattr(windows, "screens", lambda: THREE[:1])
    assert windows.screens_summary() == ""  # one screen: nothing to add


def test_screens_by_name(monkeypatch):
    monkeypatch.setattr(windows, "screens", lambda: THREE)
    assert windows.screen_named("left screen")["n"] == 1
    assert windows.screen_named("second")["n"] == 2
    assert windows.screen_named("main monitor")["n"] == 1
    assert windows.screen_named("center")["n"] == 2
    assert windows.screen_named("fourth") is None


async def test_put_a_window_on_another_screen(settings, monkeypatch):
    from test_brain_and_server import make_brain

    moved: dict = {}
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "screens", lambda: THREE)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(9, "Music - YouTube - Mozilla Firefox", "firefox.exe")])
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: moved.__setitem__("rect", (hwnd, rect)))
    monkeypatch.setattr(windows, "snap", lambda hwnd, where: moved.__setitem__("snap", (hwnd, where)))
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    brain, _ = make_brain(settings)
    brain._ctx = type("C", (), {"window": type("W", (), {"hwnd": 7})()})()
    note = await brain._arrange_windows({"layout": "maximize", "panes": ["YouTube"], "screen": "right"})
    assert note is None and moved == {"rect": (9, (3000, 0, 1080, 1872)), "snap": (9, "maximize")}
    note = await brain._arrange_windows({"layout": "maximize", "screen": "fourth"})
    assert note and "they have: left, middle, right" in note
