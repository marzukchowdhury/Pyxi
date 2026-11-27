"""Pyxi's own mouse and keyboard work is never read as the user taking the mouse.

Live, 13 November: "Paused while you use the mouse. I'll carry on when you stop." in the same
second as Pyxi's own click, and on three earlier tasks, with nobody at the mouse. The pause
is decided in ``Hands.interrupted``: the pointer is somewhere Pyxi did not put it. That is
now true only when Windows also saw input after Pyxi's own last input.

Nothing is sent and nothing real is read here: the clock, the pointer and Windows' "last
input" time are all pretend.
"""

from __future__ import annotations

import sys

import pytest

from pyxi import hands

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows input")


class Desk:
    """A pretend PC: a clock in milliseconds, a pointer, and when input was last seen."""

    def __init__(self) -> None:
        self.ms = 100_000
        self.pointer = (500, 500)
        self.input_ms = 0
        self.esc = False
        self.lag: list[tuple[int, int]] | None = None  # moves Windows has not applied yet

    def pass_ms(self, ms: int) -> None:
        self.ms += ms

    def user_moves_to(self, x: int, y: int) -> None:
        self.pointer, self.input_ms = (x, y), self.ms

    def user_presses_a_key(self) -> None:
        self.input_ms = self.ms

    def app_moves_pointer_to(self, x: int, y: int) -> None:
        self.pointer = (x, y)  # SetCursorPos by an app: the pointer moves, no input is seen


@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(hands, "_tick_now", lambda: d.ms)
    monkeypatch.setattr(hands, "_own_tick", None)
    monkeypatch.setattr(hands, "_sent_tick", None)
    monkeypatch.setattr(hands.time, "sleep", lambda s: d.pass_ms(round(s * 1000)))
    monkeypatch.setattr(hands, "is_own", lambda hwnd: False)
    monkeypatch.setattr(hands, "window_at", lambda x, y: 1)
    monkeypatch.setattr(hands, "foreground_window", lambda: 1)
    return d


@pytest.fixture
def h(desk, monkeypatch):
    hh = hands.Hands(glide_s=0.28)

    class User32:
        """Stands in for user32 under the real ``Hands._send``: Windows books the input the
        moment it is sent, a millisecond passes, and nothing reaches the real desktop."""

        def SendInput(self, count, arr, size):
            assert hands._own_tick == desk.ms and hands._sent_tick == desk.ms  # stamped before
            desk.input_ms = desk.ms
            desk.pass_ms(1)
            return count

        def VkKeyScanW(self, code):
            return 0x41  # every character is a plain key here

        def GetAsyncKeyState(self, vk):
            return 0

    def move_to(x: int, y: int) -> None:
        if desk.lag is None:
            desk.pointer = (x, y)
        else:
            desk.lag.append((x, y))

    hh._u = User32()
    monkeypatch.setattr(hh, "cursor", lambda: desk.pointer)
    monkeypatch.setattr(hh, "_tick", lambda: desk.ms)
    monkeypatch.setattr(hh, "_input_tick", lambda: desk.input_ms)
    monkeypatch.setattr(hh, "_esc", lambda: desk.esc)
    monkeypatch.setattr(hh, "_absolute", lambda x, y: (move_to(x, y), (x, y))[1])
    return hh


def test_every_input_it_sends_is_stamped_before_and_after(h, desk):
    desk.pass_ms(700)
    h.press("ctrl+b")  # the pretend SendInput checks the stamp before; a millisecond passes in it
    assert hands._own_tick == hands._sent_tick == desk.ms - 50  # and after (then press rests 50 ms)
    assert hands.recently_acted()
    before = hands._sent_tick
    desk.pass_ms(300)
    hands.acted()  # a press through the accessibility layer: Pyxi's own act, but no input sent
    assert hands._own_tick == desk.ms and hands._sent_tick == before


def test_its_own_click_is_not_the_user(h, desk):
    h.click(749, 500)  # 249 px away, as it was live
    assert desk.pointer == (749, 500)
    for wait in (0, 3, 40, 200, 1000, 5000):  # in the same second, and long after
        desk.pass_ms(wait)
        assert not h.interrupted(), wait


def test_its_own_typing_keys_and_scrolling_are_not_the_user(h, desk):
    h.click(300, 300)
    h.type("A1:C1\n")
    h.press("ctrl+b")
    h.scroll(300, 300, "down", 3)
    assert not h.interrupted()
    desk.pass_ms(4000)
    assert not h.interrupted()


def test_a_pointer_the_app_moved_after_pyxis_own_press_is_not_the_user(h, desk):
    """A press through the accessibility layer sends no input, and the app may answer it by
    moving the pointer (onto a dialog's default button, say). Nobody touched anything."""
    h.click(300, 300)
    desk.pass_ms(5000)  # the model thinks
    hands.acted()  # before the press
    desk.pass_ms(40)
    desk.app_moves_pointer_to(900, 620)
    hands.acted()  # and after it
    assert not h.interrupted()
    desk.pass_ms(2000)
    assert not h.interrupted()  # and that spot is where the pointer is taken to be from now on
    desk.app_moves_pointer_to(905, 622)
    assert not h.interrupted()


def test_a_pointer_that_moved_with_no_input_since_pyxis_own_is_never_a_pause(h, desk):
    h.click(300, 300)
    desk.pass_ms(6000)
    desk.app_moves_pointer_to(40, 40)  # no input was seen: it was not a hand on the mouse
    assert not h.interrupted()


def test_the_user_taking_the_mouse_still_stops_it_at_once(h, desk):
    h.click(300, 300)
    desk.pass_ms(800)
    desk.user_moves_to(700, 650)
    assert h.interrupted() and "mouse at (700, 650)" in h.reason
    assert "input seen" in h.reason  # the log says what it went by


def test_the_user_taking_the_mouse_while_pyxi_types_stops_it_too(h, desk):
    h.click(300, 300)
    h.type("hello")
    desk.user_moves_to(700, 650)  # a few milliseconds after Pyxi's own last key
    assert h.interrupted()  # Pyxi was not moving the pointer, and it is nowhere near the glide
    with pytest.raises(hands.Interrupted):
        h.type("more")


def test_a_small_nudge_or_a_key_of_theirs_is_not_taking_the_mouse(h, desk):
    h.click(300, 300)
    desk.pass_ms(1000)
    desk.user_moves_to(310, 292)  # a bumped desk
    assert not h.interrupted()
    desk.pass_ms(1000)
    desk.pointer = (300, 300)
    desk.user_presses_a_key()  # push to talk: input, but the pointer is where Pyxi left it
    assert not h.interrupted()


def test_a_pointer_that_trails_far_behind_a_glide_is_still_pyxis_own(h, desk):
    """On a busy PC Windows applies the moves late. The pointer is then somewhere on the
    glide, further back than the last few steps: that used to read as a hand on the mouse."""
    h.click(100, 100)
    desk.lag = []  # from here Windows holds every move back
    seen = []
    real_check = h.check

    def check() -> None:
        if len(desk.lag) > 12:
            desk.pointer = desk.lag[len(desk.lag) - 12]  # twelve steps behind
        seen.append(h.interrupted())
        real_check()

    h.check = check
    h.move(900, 700)
    assert seen and not any(seen)
    desk.pointer = (900, 700)  # it lands
    desk.pass_ms(500)
    assert not h.interrupted()


def test_only_the_end_of_the_glide_counts_once_pyxi_is_still(h, desk):
    # The user dragging the pointer back along the way it came is them, once Pyxi has stopped.
    h.click(100, 100)
    h.click(900, 700)
    desk.pass_ms(1000)
    desk.user_moves_to(500, 400)  # the middle of that glide
    assert h.interrupted()


def test_esc_stops_it_whatever_else(h, desk):
    h.click(300, 300)
    desk.esc = True
    assert h.interrupted() and h.reason == "esc"


def test_answering_pyxis_own_card_with_the_mouse_is_not_taking_over(h, desk):
    h.click(300, 300)
    desk.pass_ms(3000)
    desk.user_moves_to(1100, 200)  # to the Allow button on Pyxi's card
    h.rest()  # the operator says so once the card is answered
    assert not h.interrupted()
    h.click(400, 400)  # and it carries on
    assert not h.interrupted()


def test_after_they_stop_it_starts_from_where_they_left_the_mouse(h, desk):
    h.click(300, 300)
    desk.pass_ms(500)
    desk.user_moves_to(800, 600)
    assert h.interrupted()
    assert h.wait_idle(quiet_s=3.0, timeout_s=30.0)  # the pretend clock runs on while it sleeps
    assert not h.interrupted()
    assert h.idle_seconds() >= 3.0


def test_the_clock_wrapping_round_is_not_input_from_the_user(h, desk):
    desk.ms = 0xFFFFFFFF - 30  # GetTickCount wraps every 49 days
    h.click(300, 300)
    desk.ms = (desk.ms + 4000) & 0xFFFFFFFF
    desk.app_moves_pointer_to(900, 620)
    assert not h.interrupted()
    assert hands._after(5, 0xFFFFFFF0) == 21 and hands._after(0xFFFFFFF0, 5) == -21


def test_a_nudge_while_pyxi_was_thinking_is_not_taking_the_mouse(h, desk):
    # Live: most pauses came a few thousandths of a second after a step was announced. The
    # pointer had been moved during the five seconds Pyxi spent thinking, by someone watching
    # with a hand resting on the mouse, and each one cost seven seconds.
    h.click(300, 300)
    desk.pass_ms(1500)
    desk.user_moves_to(700, 650)  # a nudge, and then they let go
    desk.pass_ms(3000)  # Pyxi is still thinking
    assert not h.interrupted()  # carries on from where the pointer is
    assert not h.interrupted()  # and that place is now where it expects it


def test_a_hand_still_on_the_mouse_when_pyxi_needs_it_does_pause(h, desk):
    h.click(300, 300)
    desk.pass_ms(1500)
    desk.user_moves_to(700, 650)
    desk.pass_ms(hands.ACTIVE_MS - 100)  # they are using it right now
    assert h.interrupted()
    # ... and it carries on a second after they stop, not three.
    assert hands.RESUME_S <= 1.0
    assert h.wait_idle(timeout_s=30.0)
    assert h.idle_seconds() >= hands.RESUME_S and h.idle_seconds() < 2.0
    assert not h.interrupted()
