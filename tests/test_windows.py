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
    assert "- Right screen (THE SCREENSHOT IS OF THIS ONE)" in text
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


async def test_a_split_on_a_named_screen_is_two_windows_side_by_side(settings, monkeypatch):
    """Live, four times running: "split the screen, Chrome on the right, Firefox on the left"
    with three screens made the window in front full-screen and called that a split."""
    from test_brain_and_server import make_brain

    placed: dict = {}
    snapped: list = []
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "screens", lambda: THREE)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [(7, "Untitled spreadsheet - Google Chrome", "chrome.exe"), (9, "Inbox - Mozilla Firefox", "firefox.exe")],
    )
    monkeypatch.setattr(windows, "place", lambda hwnd, *rect: placed.__setitem__(hwnd, rect))
    monkeypatch.setattr(windows, "snap", lambda hwnd, where: snapped.append((hwnd, where)))
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    brain, _ = make_brain(settings)
    brain._ctx = type("C", (), {"window": type("W", (), {"hwnd": 7})()})()
    note = await brain._arrange_windows({"layout": "split", "current_side": "right", "open": "firefox", "screen": "left"})
    left, _top, width, height = THREE[0]["work"]
    assert note is None and snapped == []  # nothing was made full-screen
    assert placed[9][0] == left and placed[7][0] > left  # Firefox on the left, Chrome beside it
    assert placed[9][2] + placed[7][2] == width and placed[9][3] == placed[7][3] == height


def test_a_song_on_a_screen_is_not_a_song_search():
    from pyxi.companion.instant import play_request

    assert play_request("Can you play the song on my middle screen?") is None
    assert play_request("Just click play on the middle screen.") is None
    assert play_request("play Blinding Lights by The Weeknd") == ("Blinding Lights by The Weeknd", "youtube_music")


# ---------------------------------------------------------------------- bringing a window forward
class _User32:
    """Windows, as far as focus() uses it."""

    def __init__(self, front: int, iconic: bool = False) -> None:
        self.front, self.iconic, self.keys, self.shown = front, iconic, [], []

    def GetForegroundWindow(self) -> int:
        return self.front

    def IsIconic(self, hwnd: int) -> bool:
        return self.iconic

    def ShowWindow(self, hwnd: int, how: int) -> None:
        self.shown.append(hwnd)
        self.iconic = False

    def keybd_event(self, key: int, scan: int, flags: int, extra: int) -> None:
        self.keys.append((key, "up" if flags & 2 else "down"))

    def SetForegroundWindow(self, hwnd: int) -> bool:
        self.front = hwnd
        return True


def test_a_window_already_in_front_gets_no_key_at_all(monkeypatch):
    """Live: a table was pasted three times into the spreadsheet in front of the founder and
    never arrived. Before each paste the window was "brought to the front" with an Alt tap,
    though it was in front already; a lone Alt moves the keyboard to the app's menu bar, and
    the paste went there."""
    u = _User32(front=7)
    monkeypatch.setattr(windows, "_user32", lambda: u)
    assert windows.focus(7) is True
    assert u.keys == [] and u.shown == []


def test_bringing_another_window_forward_never_sends_alt_alone(monkeypatch):
    u = _User32(front=3)
    monkeypatch.setattr(windows, "_user32", lambda: u)
    assert windows.focus(7) is True and u.front == 7
    # Alt is held around a key that means nothing, so the app in front opens no menu.
    assert u.keys == [(0x12, "down"), (0xE8, "down"), (0xE8, "up"), (0x12, "up")]
    # A minimized window is restored first, even when Windows still names it the front one.
    u = _User32(front=7, iconic=True)
    monkeypatch.setattr(windows, "_user32", lambda: u)
    assert windows.focus(7) is True and u.shown == [7] and u.keys


# ---------------------------------------------------------------------- browser tabs by key
class _Keys:
    """The keyboard, and the window each key went to."""

    def __init__(self, front: list[int], closes_window_after: int | None = None) -> None:
        self.front, self.sent, self.closes = front, [], closes_window_after

    def press(self, combo: str) -> None:
        self.sent.append((combo, self.front[0]))
        if combo == "ctrl+w" and self.closes is not None:
            self.closes -= 1
            if self.closes == 0:
                self.front[0] = 99  # its last tab: the window is gone, another is in front


async def _tabs(settings, monkeypatch, value, *, exe="firefox.exe", closes_window_after=None):
    from pyxi import hands
    from test_brain_and_server import make_brain

    front = [3]
    keys = _Keys(front, closes_window_after)
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(hands, "available", lambda: True)
    monkeypatch.setattr(hands, "Hands", lambda: keys)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(3, "Mail", ""), (7, "YouTube - Mozilla Firefox", "")])
    monkeypatch.setattr(windows, "exe_of", lambda hwnd: rf"C:\Apps\{exe}" if hwnd == 7 else r"C:\Apps\notepad.exe")
    monkeypatch.setattr(windows, "focus", lambda hwnd: front.__setitem__(0, hwnd) or True)
    monkeypatch.setattr(windows, "foreground", lambda: front[0])
    brain, _ = make_brain(settings)
    return await brain._quick_action("tabs", value), keys.sent


async def test_closing_the_last_four_tabs_is_five_keys_into_the_browser(settings, monkeypatch):
    """Live: "close the last four tabs" was done with the mouse, took 63 seconds and gave up."""
    note, sent = await _tabs(settings, monkeypatch, "close last 4")
    assert note is None
    assert sent == [("ctrl+9", 7), ("ctrl+w", 7), ("ctrl+w", 7), ("ctrl+w", 7), ("ctrl+w", 7)]
    for value, keys in (("close", ["ctrl+w"]), ("new", ["ctrl+t"]), ("reopen", ["ctrl+shift+t"]), ("next", ["ctrl+tab"]), ("previous", ["ctrl+shift+tab"])):
        note, sent = await _tabs(settings, monkeypatch, value)
        assert note is None and [k for k, _ in sent] == keys


async def test_tab_keys_only_ever_go_to_a_browser(settings, monkeypatch):
    # No browser open: nothing is pressed (ctrl+w in another app closes something else).
    note, sent = await _tabs(settings, monkeypatch, "close", exe="winword.exe")
    assert note == "tabs: no browser window is open." and sent == []
    # The window's last tab takes the window with it: the next ctrl+w is never sent.
    note, sent = await _tabs(settings, monkeypatch, "close last 4", closes_window_after=2)
    assert note.startswith("tabs: closed 2 of 4") and [w for _, w in sent] == [7, 7, 7]
    # Never more than ten at once, and nothing it doesn't know.
    note, sent = await _tabs(settings, monkeypatch, "close last 400")
    assert note is None and sum(k == "ctrl+w" for k, _ in sent) == 10
    note, sent = await _tabs(settings, monkeypatch, "close everything")
    assert note.startswith("tabs: I can close") and sent == []


# ---------------------------------------------------------------------- which browser a site opens in
async def test_a_site_opens_in_the_browser_they_use_not_in_edge(monkeypatch):
    """Live: "Gmail on the left, YouTube on the right", asked from another app. YouTube opened
    in Edge beside a Firefox full of their tabs. "I think you opened Microsoft Edge, I want
    you to work on Mozilla Firefox." """
    started: list[list[str]] = []
    exes = {3: r"C:\Apps\GenOffice.exe", 7: r"C:\Program Files\Mozilla Firefox\firefox.exe"}
    monkeypatch.setattr(windows.subprocess, "Popen", lambda args, **kw: started.append(list(args)))
    monkeypatch.setattr(windows, "_visible_windows", lambda: set())
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(3, "Untitled.xlsx", ""), (7, "Inbox - Mozilla Firefox", "")])
    monkeypatch.setattr(windows, "exe_of", lambda hwnd: exes.get(hwnd, ""))
    monkeypatch.setattr(windows.asyncio, "sleep", _no_sleep)
    # Asked from GenOffice: the Firefox they have open, in a new window of its own.
    await windows.open_new_window("https://www.youtube.com", like=3)
    assert started[-1] == [exes[7], "-new-window", "https://www.youtube.com"]
    # No browser open: their default one.
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(3, "Untitled.xlsx", "")])
    monkeypatch.setattr(windows, "default_browser", lambda: r"C:\Apps\chrome.exe")
    await windows.open_new_window("https://www.youtube.com", like=3)
    assert started[-1] == [r"C:\Apps\chrome.exe", "--new-window", "https://www.youtube.com"]
    # Neither, or one Pyxi can't place: Edge, as before.
    monkeypatch.setattr(windows, "default_browser", lambda: "")
    await windows.open_new_window("https://www.youtube.com", like=3)
    assert started[-1][:5] == ["cmd", "/c", "start", "", "msedge"]


async def _no_sleep(seconds: float) -> None:
    return None


def test_a_site_opens_in_the_browser_they_name_or_are_in(monkeypatch):
    """Live: "open Google Sheets on Chrome", three times. Each opened in Edge, the default
    browser, behind the Chrome window in front. "Still not done." """
    real = windows.browser_for.real  # the test set-up turns it off: this is the real one
    exes = {
        3: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        7: r"C:\Program Files\Mozilla Firefox\firefox.exe",
        9: r"C:\Apps\GenOffice.exe",
    }
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(9, "Sheet", ""), (7, "Inbox - Mozilla Firefox", ""), (3, "New Tab - Google Chrome", "")])
    monkeypatch.setattr(windows, "exe_of", lambda hwnd: exes.get(hwnd, ""))
    monkeypatch.setattr(windows, "default_browser", lambda: r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    monkeypatch.setattr(windows, "_program_path", lambda name: rf"C:\Found\{name}")
    assert real("open Google Sheets on Chrome") == exes[3]  # the one they name
    assert real("open it in Brave") == r"C:\Found\brave.exe"  # named, not open: where it is installed
    assert real("open YouTube", like=3) == exes[3]  # the one they are in
    assert real("open YouTube", like=9) == exes[7]  # not in a browser: the one they have open
    # Their default browser is the one in front: nothing special, the link opens the usual way.
    monkeypatch.setattr(windows, "default_browser", lambda: exes[7])
    assert real("open YouTube", like=7) == ""


def test_a_site_opens_in_the_window_they_are_in_not_that_browsers_other_one(monkeypatch):
    """Live, three times running: "open techlandbd.com on a new tab", with Firefox in front of
    them and a second Firefox window on another screen. The tab went to the other window each
    time. The window they are in is brought to the front first, when it is that browser's."""
    did: list = []
    firefox = r"C:\Apps\firefox.exe"
    monkeypatch.setattr(windows, "exe_of", lambda hwnd: {5: firefox, 6: r"C:\Apps\Code.exe"}.get(hwnd, ""))
    monkeypatch.setattr(windows, "focus", lambda hwnd: did.append(("focus", hwnd)) or True)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda args: did.append(("open", args[1])))
    monkeypatch.setattr(windows.time, "sleep", lambda s: None)
    open_in = windows.open_in.real  # the test set-up turns it off: this is the real one
    open_in(firefox, "https://techlandbd.com", 5)
    assert did == [("focus", 5), ("open", "https://techlandbd.com")]
    did.clear()
    open_in(firefox, "https://techlandbd.com", 6)  # they are in another program: nothing is moved
    open_in(firefox, "https://techlandbd.com")
    assert did == [("open", "https://techlandbd.com"), ("open", "https://techlandbd.com")]
