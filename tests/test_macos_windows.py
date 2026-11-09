"""The Mac window list, screens, snapping and the Accessibility tree, on a fake Mac."""

from __future__ import annotations

import sys

import pytest

from macfakes import El, FakeMac, install, rect
from pyxi.macos import uia as mac_uia
from pyxi.macos import windows as mac_windows


@pytest.fixture
def mac(monkeypatch) -> FakeMac:
    monkeypatch.setattr(sys, "platform", "darwin")
    m = install(monkeypatch)
    # A 1440x900 MacBook screen (menu bar 25 pt, Dock 70 pt at the bottom) and a 1920x1080
    # monitor to its left whose top sits 180 pt higher. Cocoa counts y up from the bottom.
    m.screens = [
        (rect(0, 0, 1440, 900), rect(0, 70, 1440, 805)),
        (rect(-1920, 0, 1920, 1080), rect(-1920, 0, 1920, 1055)),
    ]
    m.add_app(101, "Safari")
    m.add_app(202, "Notes")
    m.add_app(303, "Pyxi")
    m.add_app(404, "Dock", "/System/Library/CoreServices/Dock.app")
    m.add_window(1, 303, "Pyxi overlay", 0, 0, 1440, 900, layer=1000)  # the overlay: high level
    m.add_window(10, 101, "Apple - Start", 0, 25, 900, 700)
    m.add_window(11, 202, "Shopping list", 500, 100, 600, 500)
    m.add_window(12, 202, "", 0, 0, 30, 20)  # a tiny helper window
    m.add_window(13, 101, "Old tab", 100, 100, 800, 600, onscreen=False)  # minimized
    m.add_window(14, 404, "Dock", 0, 830, 1440, 70)
    m.frontmost = 101
    return m


def test_screens_are_flipped_to_top_left_and_named_left_to_right(mac):
    found = mac_windows.screens()
    assert [s["name"] for s in found] == ["left", "right"]
    left, right = found
    assert left["rect"] == (-1920, -180, 1920, 1080)
    assert right["rect"] == (0, 0, 1440, 900) and right["primary"]
    assert right["work"] == (0, 25, 1440, 805)  # below the menu bar, above the Dock
    assert not left["primary"]


def test_open_windows_lists_app_windows_frontmost_first(mac):
    found = mac_windows.open_windows()
    assert [(h, t) for h, t, _ in found] == [(10, "Apple - Start"), (11, "Shopping list")]
    assert found[0][2] == "/Applications/Safari.app/Contents/MacOS/Safari"
    with_minimized = [h for h, _t, _e in mac_windows.open_windows(minimized=True)]
    assert 13 in with_minimized


def test_without_screen_recording_a_window_is_listed_by_its_app(mac):
    for w in mac.windows:
        w["kCGWindowName"] = ""  # what macOS gives without the permission
    titles = [t for _h, t, _e in mac_windows.open_windows()]
    assert titles == ["Safari", "Notes"]


def test_rect_exe_same_app_and_foreground(mac):
    assert mac_windows.rect_of(11) == (500, 100, 600, 500)
    assert mac_windows.rect_of(999) is None
    assert mac_windows.exe_of(11).endswith("Notes")
    assert mac_windows.same_app(10, 13) and not mac_windows.same_app(10, 11)
    assert mac_windows.foreground() == 10
    assert mac_windows.is_minimized(13)  # off screen, and AX doesn't say otherwise
    assert not mac_windows.is_minimized(11)
    mac.apps[202].ax.attrs["AXWindows"][0].attrs["AXMinimized"] = True
    assert mac_windows.is_minimized(11)  # AX's word wins


def test_snap_left_fills_half_the_work_area_through_accessibility(mac):
    mac_windows.snap(11, "left")
    notes = mac.apps[202].ax.attrs["AXWindows"][0]
    assert notes.attrs["AXPosition"].value.x == 0 and notes.attrs["AXPosition"].value.y == 25
    assert notes.attrs["AXSize"].value.width == 720 and notes.attrs["AXSize"].value.height == 805
    assert mac_windows.rect_of(11) == (0, 25, 720, 805)  # the window list follows


def test_snap_minimize_and_maximize(mac):
    mac_windows.snap(11, "minimize")
    notes = mac.apps[202].ax.attrs["AXWindows"][0]
    assert notes.attrs["AXMinimized"] is True
    mac_windows.snap(11, "maximize")
    assert notes.attrs["AXMinimized"] is False
    assert mac_windows.rect_of(11) == (0, 25, 1440, 805)


def test_focus_activates_the_app_and_raises_the_window(mac):
    assert mac_windows.focus(11)
    assert mac.apps[202].activated == 1
    assert "AXRaise" in mac.apps[202].ax.attrs["AXWindows"][0].actions
    assert mac_windows.foreground() == 11


def test_ax_window_matches_by_frame_not_only_title(mac):
    from pyxi.macos import _ax

    mac.add_window(15, 202, "Shopping list", 700, 300, 400, 300)  # same title, another window
    assert _ax.ax_window(15) is mac.apps[202].ax.attrs["AXWindows"][-1]


def test_new_window_commands_per_browser(mac):
    assert mac_windows._new_window_command("https://x.com", None) == ["open", "https://x.com"]
    mac.add_app(505, "Google Chrome")
    mac.add_window(20, 505, "New Tab", 0, 0, 800, 600)
    cmd = mac_windows._new_window_command("https://x.com", 20)
    assert cmd == ["open", "-na", "/Applications/Google Chrome.app", "--args", "--new-window", "https://x.com"]
    safari = mac_windows._new_window_command('https://x.com/"a"', 10)
    assert safari[0] == "osascript" and '\\"a\\"' in safari[2]


# ---------------------------------------------------------------------- the Accessibility tree
def _tree(mac: FakeMac) -> El:
    notes = mac.apps[202].ax.attrs["AXWindows"][0]
    field = El(202, "AXTextField", AXDescription="Search").place(520, 130, 200, 24)
    secret = El(202, "AXTextField", AXSubrole="AXSecureTextField", AXTitle="Password").place(520, 170, 200, 24)
    tab = El(202, "AXRadioButton", AXTitle="All Notes").place(740, 130, 80, 24)
    tabs = El(202, "AXTabGroup", AXTitle="").place(740, 125, 300, 40).kids(tab)
    label = El(202, "AXStaticText", AXValue="Milk, eggs").place(520, 220, 300, 20)
    blank = El(202, "AXStaticText").place(520, 250, 300, 20)
    far = El(202, "AXButton", AXTitle="Scrolled away").place(520, 5000, 80, 24)
    ok = El(202, "AXButton", AXTitle="OK").place(1000, 550, 60, 24)
    group = El(202, "AXGroup", AXTitle="Editor").place(510, 120, 580, 470).kids(field, secret, tabs, label, blank, far)
    notes.kids(group, ok)
    return notes


def test_read_foreground_walks_the_tree_with_windows_role_names(mac):
    _tree(mac)
    window, els = mac_uia.read_foreground(hwnd=11)
    assert window.title == "Shopping list" and window.process == "Notes" and window.hwnd == 11
    by_name = {e.name: e for e in els}
    assert by_name["Editor"].role == "Group"
    assert by_name["Search"].role == "Edit" and by_name["Search"].rect == (520, 130, 200, 24)
    assert by_name["Password"].is_password
    assert by_name["All Notes"].role == "TabItem"  # a radio button in a tab group
    assert by_name["Milk, eggs"].role == "Text"
    assert by_name["OK"].role == "Button" and by_name["OK"].parent is None
    assert "Scrolled away" not in by_name  # outside the window
    assert all(e.name for e in els if e.role == "Text")  # unnamed labels are dropped
    editor = els.index(by_name["Editor"])
    assert by_name["Search"].parent == editor and by_name["Search"].depth == 2


def test_read_foreground_follows_the_mouse(mac):
    _tree(mac)
    mac.cursor = (900, 400)  # past Safari's right edge, over Notes
    window, _els = mac_uia.read_foreground()
    assert window.hwnd == 11
    mac.cursor = (100, 100)
    window, _els = mac_uia.read_foreground()
    assert window.hwnd == 10


def test_read_foreground_without_accessibility_reads_nothing(mac):
    mac.trusted = False
    assert mac_uia.read_foreground(hwnd=11) == (None, [])


def test_window_at_skips_pyxi_and_overlays(mac, monkeypatch):
    monkeypatch.setenv("PYXI_APP_PID", "303")
    mac.add_window(16, 303, "Pyxi", 50, 50, 200, 200)  # Pyxi's panel, a normal-level window
    mac.windows.insert(0, mac.windows.pop())  # in front of everything
    assert mac_uia.window_at(100, 100) == 10
    assert mac_uia.window_at(5000, 5000) is None


def test_read_taskbar_reads_the_dock(mac):
    item = El(404, "AXDockItem", AXTitle="Spotify").place(600, 840, 50, 50)
    tiny = El(404, "AXDockItem", AXTitle="Separator").place(700, 840, 2, 50)
    mac.apps[404].ax.kids(El(404, "AXList").kids(item, tiny))
    found = mac_uia.read_taskbar()
    assert [(b.name, b.role, b.rect) for b in found] == [("Spotify", "TaskbarButton", (600, 840, 50, 50))]


def test_read_focus_and_focused_field_never_read_passwords(mac):
    win = mac.apps[202].ax.attrs["AXWindows"][0]
    mac.focused = El(202, "AXTextField", AXTitle="Title", AXValue="Groceries", AXWindow=win).place(1, 2, 3, 4)
    f = mac_uia.read_focus()
    assert (f.process, f.window_title, f.name, f.role) == ("Notes", "Shopping list", "Title", "Edit")
    assert mac_uia.focused_field()["value"] == "Groceries"
    mac.focused.attrs["AXSubrole"] = "AXSecureTextField"
    assert mac_uia.read_focus().name == "(password field)"
    field = mac_uia.focused_field()
    assert field["password"] and field["value"] == ""


def test_browser_url_from_the_address_field(mac):
    win = mac.apps[101].ax.attrs["AXWindows"][0]
    bar = El(101, "AXTextField", AXDescription="Smart Search Field", AXValue="apple.com/mac")
    win.kids(El(101, "AXToolbar").kids(bar))
    assert mac_uia.browser_url(10) == "https://apple.com/mac"


def test_browser_url_falls_back_to_applescript(mac, monkeypatch):
    calls = []

    class Done:
        stdout = "https://www.apple.com/\n"

    def run(cmd, **_kw):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(mac_uia.subprocess, "run", run)
    assert mac_uia.browser_url(10) == "https://www.apple.com/"
    assert calls[0][:2] == ["osascript", "-e"] and "Safari" in calls[0][2]


def test_cursor_and_titles(mac):
    mac.cursor = (12.6, 40.2)
    assert mac_uia.cursor_pos() == (12, 40)
    assert mac_uia.window_title(11) == "Shopping list"
    assert mac_uia.window_title(999) is None
    assert mac_uia.process_name(101) == "Safari"
