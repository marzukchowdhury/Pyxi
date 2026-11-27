"""Time Pyxi's hands on real Windows apps: a short, repeatable set of everyday desktop tasks.

Runs the real operator (the real screen reader, the real model, real input) against apps that
are on every PC and hold nothing personal: Notepad, Calculator, Paint, File Explorer in a temp
folder made here, and the Settings app (read only). For each task it records whether it was
done (checked here, on disk or on screen, never taken from the model's word alone), the
seconds, the looks, the model answers and the cost.

What it will not do:
* It only works in windows that it opened itself. Every window that was open before a task is
  fenced off: a click, a key or a press that would land on one is refused, and a look that
  finds one of them in front ends the try (it is run again, not counted as a failure).
* Risky steps (send, delete, buy) are always declined. The Settings task changes nothing: a
  click on a switch, a slider or a choice there is refused.
* It closes its own windows and removes its temp folder when it is done.
* Notepad brings back the tabs that were open the last time it was used. Those are not ours:
  their names are taken out of what the model is shown (the list, the text and the picture),
  and at the end only the tab this script opened is closed, so nothing of ours is left in
  the remembered session.
* If the person at the PC uses the mouse, the task is run again rather than counted as failed.

usage: python scripts/desktop_bench.py [--only name,name] [--label before] [--out file.json]
       python scripts/desktop_bench.py --list
       --no-direct   mouse and keyboard only: nothing is pressed or set through accessibility
       --screen 2    put the bench's own form on that screen first (the form tasks only)
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from otto.config import load_dotenv
from PIL import ImageDraw

from pyxi import hands, windows
from pyxi.companion.operator import Operator
from pyxi.config import PyxiSettings
from pyxi.screen import ScreenContext, ScreenReader
from pyxi.screen import press as presses
from pyxi.screen.context import WindowInfo, enable_dpi_awareness
from pyxi.stream_llm import StreamingChat

TASK_LIMIT_S = 240.0
TASK_COST = 0.15  # dollars: a task that costs more than this is stopped
RERUNS = 2  # tries again when the person at the PC took the mouse

PARAGRAPH = "The harbour opens at nine. Bring the blue folder and two spare keys."
u32 = ctypes.windll.user32 if sys.platform == "win32" else None
for_hands: dict[str, Any] = {}  # how the look is asked for: as the app asks for it
NO_DIRECT = False  # --no-direct
ON_SCREEN = 0  # --screen N


# ---------------------------------------------------------------- windows of our own
def all_windows() -> set[int]:
    """Every top-level window there is, shown or not."""
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        found.add(int(hwnd))
        return True

    u32.EnumWindows(visit, 0)
    return found


def title_of(hwnd: int) -> str:
    n = u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def cloaked(hwnd: int) -> bool:
    """Kept by Windows but not shown (a store app that is asleep)."""
    value = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def pid_of(hwnd: int) -> int:
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def root_of(hwnd: int) -> int:
    u = ctypes.WinDLL("user32")
    u.GetAncestor.restype = wt.HWND
    u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    return int(u.GetAncestor(hwnd, 2) or 0) or int(hwnd or 0)


def wait_window(before: set[int], words: str, timeout_s: float = 45.0) -> int:
    """The new window whose title has these words; 0 when none came."""
    end = time.monotonic() + timeout_s
    want = words.lower()
    while time.monotonic() < end:
        for hwnd in all_windows() - before:
            if u32.IsWindowVisible(hwnd) and want in title_of(hwnd).lower() and not cloaked(hwnd):
                return hwnd
        time.sleep(0.2)
    return 0


def close_ours(before: set[int], kill: bool = False) -> None:
    """Close every window that was not there before the task: dialogs first, then the rest.
    ``kill``: end the program instead when all of its windows are ours (Paint asks to save)."""
    WM_CLOSE = 0x0010
    ours = [h for h in all_windows() - before if u32.IsWindowVisible(h) and title_of(h)]
    theirs = {pid_of(h) for h in before}
    if kill:
        for pid in {pid_of(h) for h in ours} - theirs:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        time.sleep(0.3)
        return
    for _ in range(3):
        ours = [h for h in all_windows() - before if u32.IsWindowVisible(h) and title_of(h)]
        if not ours:
            return
        ours.sort(key=lambda h: not u32.GetWindow(h, 4))  # owned ones (dialogs) first
        for h in ours:
            u32.PostMessageW(h, WM_CLOSE, 0, 0)
        time.sleep(0.8)
        for h in [h for h in all_windows() - before if u32.IsWindowVisible(h)]:
            _dont_save(h)
    for pid in {pid_of(h) for h in all_windows() - before if u32.IsWindowVisible(h) and title_of(h)} - theirs:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)


def _dont_save(hwnd: int) -> None:
    """A "save your changes?" question in one of our windows: answered no."""
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            top = auto.ControlFromHandle(hwnd)
            for control, _ in auto.WalkControl(top, maxDepth=16):
                if control.ControlTypeName == "ButtonControl" and re.fullmatch(
                    r"(?i)don.?t save|no", (control.Name or "").strip()
                ):
                    control.GetInvokePattern().Invoke()
                    return
    except Exception:
        pass


# ---------------------------------------------------------------- Notepad's remembered tabs
def _tab(name: str) -> str:
    return re.sub(r"\. (Unmodified|Modified)\.$", "", name or "").strip()


def notepad_tabs(hwnd: int) -> list[tuple[str, bool]]:
    """Each tab of a Notepad window: its file's name, and whether it is the one in front."""
    out: list[tuple[str, bool]] = []
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            top = auto.ControlFromHandle(hwnd)
            for control, _ in auto.WalkControl(top, maxDepth=12):
                if control.ControlTypeName == "TabItemControl":
                    chosen = False
                    try:
                        chosen = bool(control.GetSelectionItemPattern().IsSelected)
                    except Exception:
                        pass
                    out.append((_tab(control.Name), chosen))
    except Exception:
        pass
    return out


def hide_their_tabs(ctx: ScreenContext, theirs: set[str]) -> None:
    """Tabs Notepad brought back from an earlier session are not ours to show the model: out
    of the list, out of the text, black in the picture."""
    hidden = [e for e in ctx.elements if e.role == "TabItem" and _tab(e.name) in theirs]
    if not hidden:
        return
    ids = {e.id for e in hidden}
    rects = [e.rect for e in hidden]
    ctx.elements = [e for e in ctx.elements if e.id not in ids and e.parent not in ids]

    def on_one(r: tuple[int, int, int, int]) -> bool:
        cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
        return any(x <= cx <= x + w and y <= cy <= y + h for x, y, w, h in rects)

    ctx.text = [t for t in ctx.text if not on_one(t.rect)]
    if ctx.screenshot is not None:
        draw = ImageDraw.Draw(ctx.screenshot)
        for x, y, w, h in rects:
            draw.rectangle([x - ctx.origin[0], y - ctx.origin[1], x - ctx.origin[0] + w, y - ctx.origin[1] + h], fill=(0, 0, 0))


def close_our_tabs(hwnd: int, theirs: set[str]) -> None:
    """Close the tabs this script opened in a Notepad window, by Notepad's own menu, saving
    nothing: what Notepad remembers for next time is then only what it remembered before."""
    try:
        from pyxi.screen.control import Aim, Controls
    except ImportError:  # timing an older Pyxi (PYTHONPATH at its source): this repo's copy
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "pyxi_bench_control", Path(__file__).resolve().parent.parent / "src" / "pyxi" / "screen" / "control.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["pyxi_bench_control"] = module
        spec.loader.exec_module(module)
        Aim, Controls = module.Aim, module.Controls

    for h in [h for h in all_windows() if u32.IsWindowVisible(h) and u32.GetWindow(h, 4) == hwnd]:
        u32.PostMessageW(h, 0x0010, 0, 0)  # a Save as window left open
    time.sleep(0.4)
    windows.focus(hwnd)
    time.sleep(0.3)
    controls = Controls()
    for _ in range(4):
        tabs = notepad_tabs(hwnd)
        ours = [(name, chosen) for name, chosen in tabs if name not in theirs]
        if not ours or not u32.IsWindow(hwnd):
            return
        if not ours[0][1]:
            controls.select(Aim(hwnd), ours[0][0])
            time.sleep(0.5)
            now = [name for name, chosen in notepad_tabs(hwnd) if chosen]
            if now != [ours[0][0]]:
                return  # the tab in front is not ours: nothing is closed
        controls.menu(hwnd, ["File", "Close tab"])
        time.sleep(0.8)
        _dont_save(hwnd)
        time.sleep(0.5)


class Fenced(Exception):
    def __init__(self) -> None:
        super().__init__("that is outside the window this task is in, so it was not done. Stay in this window")


def fenced_hands(before: set[int]) -> type:
    """Hands that refuse anything that would land on a window that was open before."""

    class FencedHands(hands.Hands):
        def _point(self, x: int, y: int) -> None:
            if hands.window_at(x, y) in before:
                raise Fenced

        def _keys(self) -> None:
            if root_of(hands.foreground_window()) in before:
                raise Fenced

        def click(self, x, y, button="left", count=1):
            self._point(x, y)
            super().click(x, y, button, count)

        def drag(self, x0, y0, x1, y1):
            self._point(x0, y0)
            self._point(x1, y1)
            super().drag(x0, y0, x1, y1)

        def scroll(self, x, y, direction="down", notches=5):
            self._point(x, y)
            super().scroll(x, y, direction, notches)

        def press(self, combo):
            self._keys()
            super().press(combo)

        def type(self, text, per_char_s=0.014, settle_s=0.16):
            self._keys()
            super().type(text, per_char_s, settle_s)

    return FencedHands


# ---------------------------------------------------------------- the tasks
@dataclass
class Case:
    name: str
    app: str
    task: str
    start: Callable[[Path], list[str]]  # makes its files; returns the command to start the app
    title: str  # words in the title of the window that opens
    check: Callable[[Path, str, dict], str]  # "" when done, else what is wrong
    read_only: bool = False
    kill: bool = False
    before_shot: bool = False


def _write(path: Path, text: str, age_days: float = 0.0) -> None:
    path.write_bytes(text.encode("utf-8"))
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))


def _notepad(name: str, text: str) -> Callable[[Path], list[str]]:
    def start(folder: Path) -> list[str]:
        _write(folder / name, text)
        return ["notepad.exe", str(folder / name)]

    return start


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig", "replace").replace("\r\n", "\n")
    except OSError:
        return ""


def _explorer(make: Callable[[Path], None]) -> Callable[[Path], list[str]]:
    def start(folder: Path) -> list[str]:
        make(folder)
        return ["explorer.exe", str(folder)]

    return start


def _sort_files(folder: Path) -> None:
    for name, age in (("alpha.txt", 9), ("beta.txt", 30), ("gamma.txt", 1), ("delta.txt", 4), ("epsilon.txt", 16)):
        _write(folder / name, name, age)


def _many_files(folder: Path) -> None:
    for i in range(1, 301):
        _write(folder / f"report_{i:03d}.txt", str(i))


def _digits(text: str) -> str:
    return re.sub(r"[,\s  ]", "", text)


def _selected(extra: dict) -> list[str]:
    """Names of the rows chosen in our Explorer window."""
    out: list[str] = []
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            top = auto.ControlFromHandle(extra["hwnd"])
            for control, _ in auto.WalkControl(top, maxDepth=14):
                if control.ControlTypeName == "ListItemControl":
                    try:
                        if control.GetSelectionItemPattern().IsSelected:
                            out.append(control.Name or "")
                    except Exception:
                        continue
    except Exception:
        pass
    return out


def _changed(extra: dict, red: bool = False) -> int:
    """Pixels of the drawing area that are not as they were (or that are red now and were not)."""
    a, b = extra.get("shot_before"), extra.get("shot_after")
    if a is None or b is None or a.size != b.size:
        return 0
    w, h = a.size
    box = (int(w * 0.12), int(h * 0.32), int(w * 0.9), int(h * 0.9))  # under the ribbon
    pa, pb = a.crop(box).convert("RGB").load(), b.crop(box).convert("RGB").load()
    n = 0
    for y in range(box[3] - box[1]):  # every row: a pencil line is one pixel thick
        for x in range(0, box[2] - box[0], 2):
            p, q = pa[x, y], pb[x, y]
            if red:
                n += (q[0] > 180 and q[1] < 90 and q[2] < 90) and not (p[0] > 180 and p[1] < 90 and p[2] < 90)
            else:
                n += abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2]) > 90
    return n


# A small form of our own, for what the built-in apps do not have: a list of 5,000 rows in
# no order (nothing to scroll to by guessing), a dropdown of 40 choices, switches, a menu
# three deep and a message that pops up. Windows' own PowerShell draws it; it writes what
# was chosen to a file, which is what the checks read.
FORM_PS1 = r"""
param([string]$Folder)
Add-Type -AssemblyName PresentationFramework
[xml]$x = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="Bench form" Width="940" Height="660" WindowStartupLocation="CenterScreen">
 <DockPanel>
  <Menu DockPanel.Dock="Top">
   <MenuItem Header="File">
    <MenuItem Header="Export"><MenuItem Header="As CSV" Name="csv"/><MenuItem Header="As PDF" Name="pdf"/></MenuItem>
    <MenuItem Header="Reload" Name="reload"/>
   </MenuItem>
   <MenuItem Header="View"><MenuItem Header="Compact rows" IsCheckable="True" Name="compact"/></MenuItem>
  </Menu>
  <StackPanel DockPanel.Dock="Right" Width="300" Margin="14">
   <TextBlock Text="Contact name" Margin="0,0,0,4"/>
   <TextBox Name="contact" AutomationProperties.Name="Contact name" Margin="0,0,0,12"/>
   <TextBlock Text="Billing cycle" Margin="0,0,0,4"/>
   <ComboBox Name="cycle" AutomationProperties.Name="Billing cycle" Margin="0,0,0,12"/>
   <CheckBox Name="news" Content="Monthly newsletter" Margin="0,0,0,8"/>
   <CheckBox Name="vip" Content="Priority support" IsChecked="True" Margin="0,0,0,16"/>
   <Button Name="apply" Content="Apply" Width="120" HorizontalAlignment="Left"/>
   <TextBlock Name="status" Text="Nothing applied yet" Margin="0,14,0,0" TextWrapping="Wrap"/>
  </StackPanel>
  <ListBox Name="rows" AutomationProperties.Name="Customers" Margin="14" IsTextSearchEnabled="False"/>
 </DockPanel>
</Window>
'@
$w = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $x))
$get = { param($n) $w.FindName($n) }
$rows = & $get 'rows'; $cycle = & $get 'cycle'; $contact = & $get 'contact'
$news = & $get 'news'; $vip = & $get 'vip'; $status = & $get 'status'
$rows.ItemsSource = [IO.File]::ReadAllLines((Join-Path $Folder 'customers.txt'))
$cycle.ItemsSource = [IO.File]::ReadAllLines((Join-Path $Folder 'cycles.txt'))
$script:menu = ''; $script:applied = 0; $script:closed = 0
$save = {
  $o = [ordered]@{ selected = [string]$rows.SelectedItem; contact = $contact.Text; cycle = [string]$cycle.SelectedItem
    news = [bool]$news.IsChecked; vip = [bool]$vip.IsChecked; applied = $script:applied; menu = $script:menu; closed = $script:closed }
  [IO.File]::WriteAllText((Join-Path $Folder 'state.json'), ($o | ConvertTo-Json))
}
$rows.Add_SelectionChanged({ & $save })
(& $get 'apply').Add_Click({ $script:applied++; $status.Text = "Applied for " + $contact.Text; & $save })
foreach ($n in 'csv', 'pdf', 'reload') {
  (& $get $n).Add_Click({ param($s, $e) $script:menu = [string]$s.Header; & $save
    [void][Windows.MessageBox]::Show($w, "Finished: " + $s.Header, 'Bench form'); $script:closed++; & $save })
}
& $save
[void]$w.ShowDialog()
"""
CUSTOMER = "Nadia Farouk"


def _form(folder: Path) -> list[str]:
    import random

    first = ["Amara", "Bao", "Carmen", "Dario", "Elif", "Farid", "Greta", "Hugo", "Imani", "Jonas", "Keiko", "Luca", "Mina", "Nadia", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Talia", "Uma", "Viktor", "Wren", "Xavier", "Yara", "Zane", "Anouk", "Bram", "Celia", "Dov", "Enzo", "Freya", "Gus", "Hana", "Ivo", "Jade", "Kofi", "Lena", "Milo", "Nora", "Otis", "Pia", "Rafe", "Sana", "Teo", "Ula", "Vera", "Wim", "Xena", "Yusuf", "Zora", "Arlo", "Bea", "Cato", "Dina", "Eero", "Fay", "Gil", "Hedy", "Ian", "Juno", "Kai", "Lior", "Maud", "Nico", "Ola", "Pau", "Rhea", "Sami", "Tove"]
    last = ["Abara", "Bergstrom", "Castellan", "Dimitrov", "Eklund", "Farouk", "Galvan", "Hoshino", "Ibarra", "Jankowski", "Kovac", "Lindqvist", "Marchetti", "Novak", "Okafor", "Petrov", "Quist", "Rahimi", "Sorensen", "Takeda", "Umar", "Valdez", "Wieczorek", "Xu", "Yilmaz", "Zubiri", "Adeyemi", "Brandt", "Corbin", "Dalgaard", "Esposito", "Fonseca", "Grieve", "Halloran", "Ishikawa", "Jurado", "Kessler", "Lachance", "Mbeki", "Nakamura", "Osei", "Pallas", "Quarles", "Rosales", "Stavros", "Tamm", "Ueda", "Varga", "Whitlock", "Yoder", "Zamora", "Alder", "Bexley", "Cormac", "Drummond", "Eastman", "Fairley", "Gantry", "Hadley", "Ingram", "Jessop", "Kirkby", "Lowell", "Marlow", "Norcross", "Ormond", "Pinder", "Radley", "Sefton", "Thorne", "Upton", "Vance"]
    names = [f"{a} {b}" for a in first for b in last]
    random.Random(7).shuffle(names)
    names.remove(CUSTOMER)
    names.insert(3817, CUSTOMER)
    _write(folder / "customers.txt", "\r\n".join(names[:5000]))
    cycles = [f"Every {n} weeks" for n in range(2, 31)] + ["Weekly", "Monthly", "Quarterly", "Twice a year", "Yearly", "On request"]
    random.Random(3).shuffle(cycles)
    _write(folder / "cycles.txt", "\r\n".join(cycles))
    _write(folder / "form.ps1", FORM_PS1)
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(folder / "form.ps1"), "-Folder", str(folder)]


def _state(folder: Path) -> dict:
    try:
        return json.loads((folder / "state.json").read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _form_filled(f: Path, s: str, x: dict) -> str:
    st = _state(f)
    want = {"contact": "Rosa Lindqvist", "cycle": "Quarterly", "news": True, "vip": False}
    off = {k: st.get(k) for k, v in want.items() if st.get(k) != v}
    if not st.get("applied"):
        off["applied"] = st.get("applied")
    return "" if not off else f"the form holds {off}"


def _stray_copy() -> None:
    """A copy the Save as window put in Documents instead (its own choice of folder): ours, removed."""
    stray = Path.home() / "Documents" / "minutes_copy.txt"
    if "garden club" in _read(stray):
        stray.unlink()


def _scale() -> str:
    return f"{round(u32.GetDpiForSystem() * 100 / 96)}"


CASES: list[Case] = [
    Case(
        "notepad_type",
        "Notepad",
        f"In the Notepad file that is open (it is empty), type this exactly and then save it with ctrl+s: {PARAGRAPH}",
        _notepad("harbour_note.txt", ""),
        "harbour_note",
        lambda f, s, x: "" if PARAGRAPH in _read(f / "harbour_note.txt") else f"file holds {_read(f / 'harbour_note.txt')[:80]!r}",
    ),
    Case(
        "notepad_replace",
        "Notepad",
        "In the Notepad file that is open, use Find and Replace to change every 'colour' to 'color', then save the file with ctrl+s.",
        _notepad("paint_order.txt", "The colour of the door.\nPick a colour for the fence.\nNo colour was chosen for the gate.\n"),
        "paint_order",
        lambda f, s, x: ""
        if _read(f / "paint_order.txt").count("color") == 3 and "colour" not in _read(f / "paint_order.txt")
        else f"file holds {_read(f / 'paint_order.txt')[:90]!r}",
    ),
    Case(
        "notepad_save_as",
        "Notepad",
        "Save a copy of the Notepad file that is open as {folder}\\minutes_copy.txt (use Save as).",
        _notepad("minutes.txt", "Minutes of the garden club.\nNext meeting on the first Tuesday.\n"),
        "minutes",
        lambda f, s, x: "" if "garden club" in _read(f / "minutes_copy.txt") else "minutes_copy.txt is not in the folder",
    ),
    Case(
        "calc_multiply",
        "Calculator",
        "In the Calculator that is open, work out 482 times 37 and tell me the result.",
        lambda f: ["calc.exe"],
        "Calculator",
        lambda f, s, x: "" if "17834" in _digits(s) else f"said {s[:80]!r}",
    ),
    Case(
        "calc_chain",
        "Calculator",
        "In the Calculator that is open, add 125 and 375, divide the sum by 4, and tell me the result.",
        lambda f: ["calc.exe"],
        "Calculator",
        lambda f, s, x: "" if re.search(r"(?<![\d.])125(?![\d])", _digits(s)) else f"said {s[:80]!r}",
    ),
    Case(
        "paint_rectangle",
        "Paint",
        "In the Paint window that is open, draw a rectangle in the middle of the white canvas with the Rectangle shape tool.",
        lambda f: ["mspaint.exe"],
        "Paint",
        lambda f, s, x: "" if _changed(x) >= 60 else f"only {_changed(x)} sampled pixels of the canvas changed",
        kill=True,
        before_shot=True,
    ),
    Case(
        "paint_red_line",
        "Paint",
        "In the Paint window that is open, pick red as the colour and draw a line across the white canvas with the pencil or a brush.",
        lambda f: ["mspaint.exe"],
        "Paint",
        lambda f, s, x: "" if _changed(x, red=True) >= 20 else f"only {_changed(x, red=True)} sampled red pixels on the canvas",
        kill=True,
        before_shot=True,
    ),
    Case(
        "explorer_new_folder",
        "File Explorer",
        "In the File Explorer window that is open, make a new folder here called Invoices.",
        _explorer(lambda f: _write(f / "readme.txt", "x")),
        "",
        lambda f, s, x: "" if (f / "Invoices").is_dir() else f"folder holds {sorted(p.name for p in f.iterdir())}",
    ),
    Case(
        "explorer_rename",
        "File Explorer",
        "In the File Explorer window that is open, rename the file draft.txt to final.txt.",
        _explorer(lambda f: (_write(f / "draft.txt", "x"), _write(f / "budget.txt", "y"))),
        "",
        lambda f, s, x: "" if (f / "final.txt").is_file() and not (f / "draft.txt").exists() else f"folder holds {sorted(p.name for p in f.iterdir())}",
    ),
    Case(
        "explorer_sort",
        "File Explorer",
        "In the File Explorer window that is open, sort the files by date modified with the newest first, and tell me the name of the file that is then at the top.",
        _explorer(_sort_files),
        "",
        lambda f, s, x: "" if "gamma" in s.lower() else f"said {s[:80]!r}",
    ),
    Case(
        "explorer_long_list",
        "File Explorer",
        "In the File Explorer window that is open there are 300 files. Select the one called report_217.txt (just select it, do not open it).",
        _explorer(_many_files),
        "",
        lambda f, s, x: "" if any(n.startswith("report_217") for n in _selected(x)) else f"selected: {_selected(x)[:3]}",
    ),
    Case(
        "form_long_list",
        "Bench form",
        f"In the Bench form window, choose the customer called {CUSTOMER} in the Customers list (it has 5,000 rows in no order).",
        _form,
        "Bench form",
        lambda f, s, x: "" if _state(f).get("selected") == CUSTOMER else f"chosen: {_state(f).get('selected')!r}",
        kill=True,
    ),
    Case(
        "form_fill",
        "Bench form",
        "In the Bench form window, set Contact name to Rosa Lindqvist, set Billing cycle to Quarterly, turn Monthly newsletter on and Priority support off, then press Apply.",
        _form,
        "Bench form",
        _form_filled,
        kill=True,
    ),
    Case(
        "form_menu",
        "Bench form",
        "In the Bench form window, use the menu File, Export, As CSV, then close the message that comes up.",
        _form,
        "Bench form",
        lambda f, s, x: "" if _state(f).get("menu") == "As CSV" and _state(f).get("closed") else f"the form holds {_state(f)}",
        kill=True,
    ),
    Case(
        "settings_scale",
        "Settings",
        "In the Settings window that is open, find the display Scale setting (System, Display) and tell me what it is set to. Change nothing.",
        lambda f: ["cmd", "/c", "start", "ms-settings:"],
        "Settings",
        lambda f, s, x: "" if _scale() in _digits(s) else f"said {s[:80]!r}, it is {_scale()}%",
        read_only=True,
    ),
]


# ---------------------------------------------------------------- one run
@dataclass
class Run:
    name: str
    app: str
    done: bool = False
    said_ok: bool = False
    seconds: float = 0.0
    looks: int = 0
    answers: int = 0
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    reruns: int = 0
    model_s: float = 0.0
    look_s: float = 0.0
    wrong: str = ""
    summary: str = ""
    steps: list[str] = field(default_factory=list)


class Counting:
    """The model, with its answers counted and timed."""

    def __init__(self, llm: Any) -> None:
        self.llm, self.calls, self.seconds = llm, 0, 0.0

    async def complete(self, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            return await self.llm.complete(**kwargs)
        finally:
            self.calls += 1
            self.seconds += time.monotonic() - started


def _changes_a_setting(x: int, y: int) -> bool:
    """Is the control at this point one that changes something when clicked (a switch, a
    slider, a choice in a list of choices)?"""
    try:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            c = auto.ControlFromPoint(x, y)
            for _ in range(3):
                if c is None:
                    return False
                kind = c.ControlTypeName
                if kind in ("CheckBoxControl", "RadioButtonControl", "SliderControl", "ComboBoxControl", "SpinnerControl"):
                    return True
                try:
                    if kind == "ButtonControl" and c.GetPropertyValue(30041):  # it toggles
                        return True
                except Exception:
                    pass
                c = c.GetParentControl()
    except Exception:
        return True
    return False


async def run_case(case: Case, root: Path, settings: PyxiSettings, reader: ScreenReader, model: str, log_to: Any) -> Run:
    run = Run(case.name, case.app)
    for attempt in range(RERUNS + 1):
        folder = root / f"{case.name}_{attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        before = all_windows()
        if case.app == "Settings":
            kept = {h for h in before if title_of(h).strip().lower() == "settings" and u32.IsWindowVisible(h)}
            if any(not cloaked(h) for h in kept):
                run.wrong = "skipped: a Settings window was already open, and it is not ours to use"
                return run
            before -= kept  # Windows keeps a hidden one ready and shows that same window
        subprocess.Popen(case.start(folder))
        hwnd = wait_window(before, case.title or folder.name)
        if not hwnd:
            time.sleep(5)  # a late one is still ours to close
            close_ours(before, case.kill)
            run.wrong = f"{case.app} did not open"
            return run
        time.sleep(1.5)  # let it finish drawing itself
        if ON_SCREEN and case.app == "Bench form":  # only a window that is wholly ours is moved
            to = next((sc for sc in windows.screens() if sc["n"] == ON_SCREEN), None)
            if to:
                x, y, w, h = to["work"]
                windows.place(hwnd, x + 40, y + 60, min(w - 80, 940), min(h - 120, 660))
                time.sleep(0.5)
        windows.focus(hwnd)
        time.sleep(0.4)
        extra: dict[str, Any] = {"hwnd": hwnd}
        theirs: set[str] = set()
        if case.app == "Notepad":
            theirs = {name for name, _ in notepad_tabs(hwnd) if not (folder / name).exists()}
        if case.before_shot:
            extra["rect"] = windows.rect_of(hwnd)
            extra["shot_before"] = reader.glance(extra["rect"])
        paused, looks, look_s = False, 0, 0.0

        async def capture() -> ScreenContext:
            nonlocal looks, paused, look_s
            looks += 1
            at = time.monotonic()
            ctx = await reader.capture(pixels=True, follow_mouse=False, **for_hands)
            look_s += time.monotonic() - at
            if theirs:  # noqa: B023
                hide_their_tabs(ctx, theirs)  # noqa: B023
            if ctx.window.hwnd and root_of(ctx.window.hwnd) in before:  # noqa: B023
                paused = True  # one of their windows is in front: not ours to read or use
                return ScreenContext(captured_at=ctx.captured_at, window=WindowInfo(), excluded="another window")
            return ctx

        async def confirm(action: str, reason: str) -> bool:
            return False

        async def report(doing: str, step: int) -> None:
            nonlocal paused
            if doing.startswith("Paused"):
                paused = True

        llm = Counting(StreamingChat(model))
        strong = Counting(StreamingChat(settings.model_hands_strong)) if settings.model_hands_strong else None

        def press(target: presses.Target, *a: Any, **k: Any) -> Any:
            if root_of(target.hwnd) in before:  # noqa: B023
                return None
            return presses.press(target, *a, **k)

        more: dict[str, Any] = {}
        if "control" in inspect.signature(Operator.__init__).parameters:
            from pyxi.screen.control import Controls, Uia

            class FencedUia(Uia):
                def roots(self, hwnd: int, before: set[int] = before) -> list[int]:
                    return [h for h in super().roots(hwnd) if h not in before]

            more["control"] = None if NO_DIRECT else Controls(backend=FencedUia())
        op = Operator(
            llm,
            capture,
            confirm,
            report,
            image_width=settings.screen.model_image_width,
            strong_llm=strong,
            max_cost=TASK_COST,
            make_hands=fenced_hands(before),
            press=None if NO_DIRECT else press,
            glance=reader.glance,
            **more,
        )
        inner = op._do

        async def do(
            h: Any, ctx: ScreenContext, a: dict, n: int, case: Case = case, inner: Any = inner, where: Any = op._where
        ) -> tuple[str, str]:
            kind = str(a.get("do"))
            keys = str(a.get("text") or "").lower().replace(" ", "")
            if kind == "open" or (kind == "press" and ("win" in keys.split("+") or keys in ("alt+tab", "alt+f4"))):
                return f"{kind} {a.get('text')}", "skipped: stay in the window that is open; nothing else may be opened or switched to"
            if case.read_only:
                if kind in ("drag", "set_value", "select", "toggle"):
                    return kind, "skipped: nothing may be changed here"
                spot = (await where(ctx, a, ""))[0] if kind in ("click", "double_click") else None
                if spot and await asyncio.to_thread(_changes_a_setting, *spot):
                    return kind, "skipped: that control changes a setting, and nothing may be changed here"
            return await inner(h, ctx, a, n)

        op._do = do  # type: ignore[method-assign]
        started = time.monotonic()
        try:
            out = await asyncio.wait_for(op.run(case.task.replace("{folder}", str(folder)), hwnd), TASK_LIMIT_S)
            summary, said_ok, steps, stopped = out.summary, out.ok, out.steps, out.stopped
        except TimeoutError:
            op.cancelled = True
            summary, said_ok, steps, stopped = f"not finished in {TASK_LIMIT_S:.0f} s", False, [], False
        except Exception as exc:
            summary, said_ok, steps, stopped = f"error: {exc}", False, [], False
        seconds = time.monotonic() - started
        time.sleep(0.6)
        if case.before_shot:
            extra["shot_after"] = reader.glance(extra["rect"])
        paused = paused or stopped or any("paused while they used the mouse" in s.note for s in steps)
        wrong = case.check(folder, summary, extra)
        _stray_copy()
        usage = op.usage
        if case.app == "Notepad":
            close_our_tabs(hwnd, theirs)
        close_ours(before, case.kill)
        lines = [f"{s.n}. {s.actions}" + (f"  -> {s.note}" if s.note else "") for s in steps]
        print(f"--- {case.name} try {attempt + 1}: {'paused' if paused else ''} {seconds:.1f}s {summary}", file=log_to)
        print("\n".join(lines), file=log_to, flush=True)
        if paused and wrong and attempt < RERUNS:
            run.reruns += 1
            print(f"  {case.name}: someone was using the PC, running it again")
            time.sleep(4)
            continue
        run.done, run.said_ok, run.wrong = not wrong, said_ok, wrong
        run.seconds, run.looks = round(seconds, 1), looks
        run.answers = llm.calls + (strong.calls if strong else 0)
        run.model_s = round(llm.seconds + (strong.seconds if strong else 0.0), 1)
        run.look_s = round(look_s, 1)
        run.cost = round(usage.cost_usd, 4) if usage else 0.0
        run.tokens_in = (usage.input_tokens + usage.cache_read_tokens) if usage else 0
        run.tokens_out = usage.output_tokens if usage else 0
        run.summary, run.steps = summary, lines
        return run
    return run


def table(runs: list[Run]) -> str:
    rows = [f"{'task':22} {'done':5} {'secs':>6} {'looks':>5} {'answers':>7} {'model s':>7} {'cost':>7}  note"]
    for r in runs:
        rows.append(
            f"{r.name:22} {'yes' if r.done else 'NO':5} {r.seconds:6.1f} {r.looks:5d} {r.answers:7d} {r.model_s:7.1f} {r.cost:7.4f}  "
            f"{(r.wrong or '')[:60]}{f' (rerun x{r.reruns})' if r.reruns else ''}"
        )
    rows.append(
        f"{'TOTAL':22} {sum(r.done for r in runs):2d}/{len(runs):<2d} {sum(r.seconds for r in runs):6.1f} "
        f"{sum(r.looks for r in runs):5d} {sum(r.answers for r in runs):7d} {sum(r.model_s for r in runs):7.1f} {sum(r.cost for r in runs):7.4f}"
    )
    return "\n".join(rows)


async def main() -> None:
    args = sys.argv[1:]
    if "--list" in args:
        for c in CASES:
            print(f"{c.name:22} {c.app:14} {c.task}")
        return

    def opt(name: str, default: str = "") -> str:
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default

    if sys.platform != "win32":
        raise SystemExit("this runs on Windows only")
    load_dotenv(opt("--env", ".env"))
    logging.basicConfig(level=logging.WARNING)
    enable_dpi_awareness()
    settings = PyxiSettings()
    reader = ScreenReader(settings.screen)
    global for_hands, NO_DIRECT, ON_SCREEN
    NO_DIRECT = "--no-direct" in args
    ON_SCREEN = int(opt("--screen", "0") or 0)
    for_hands = {"hands": True} if getattr(reader, "reads_for_hands", False) else {}
    model = opt("--model") or settings.model_hands or settings.model
    if "/" not in model:
        model = "anthropic/claude-haiku-5.5"  # the same stand-in scripts/hands_check.py uses
    only = {n for n in opt("--only").split(",") if n}
    cases = [c for c in CASES if not only or c.name in only]
    label = opt("--label", "run")
    root = Path(tempfile.mkdtemp(prefix="pyxi_bench_"))
    log_path = Path(opt("--log", str(Path(tempfile.gettempdir()) / f"pyxi_bench_{label}.log")))
    runs: list[Run] = []
    print(f"model: {model}; {len(cases)} tasks; steps go to {log_path}")
    try:
        with log_path.open("a", encoding="utf-8") as log_to:
            for case in cases:
                run = await run_case(case, root, settings, reader, model, log_to)
                runs.append(run)
                print(
                    f"  {run.name}: {'done' if run.done else 'NOT DONE'} in {run.seconds:.1f}s, {run.looks} looks, "
                    f"{run.answers} answers, ${run.cost:.4f} {run.wrong}"
                )
                time.sleep(1.0)
    finally:
        for _ in range(5):
            shutil.rmtree(root, ignore_errors=True)
            if not root.exists():
                break
            time.sleep(0.5)
    print(table(runs))
    if out := opt("--out"):
        Path(out).write_bytes(json.dumps({"label": label, "model": model, "runs": [r.__dict__ for r in runs]}, indent=1).encode())


if __name__ == "__main__":
    asyncio.run(main())
