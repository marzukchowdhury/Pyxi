"""Pyxi says one short line while it works: no second "On it", no running commentary."""

from __future__ import annotations

from pyxi.companion.brain import trim_chatter


def test_a_second_on_it_in_a_turn_is_not_said() -> None:
    assert trim_chatter("On it.", said_before=True) == ""
    assert trim_chatter("On it.", said_before=False) == "On it."


def test_commentary_goes_once_something_else_is_said() -> None:
    # The tidy scenario: "On it." streamed first, then the rest of the line.
    assert trim_chatter("I'll look at what's on your desktop first.", said_before=True) == ""
    assert trim_chatter("On it. I'll look at what's on your desktop first.", said_before=False) == "On it."
    # The check-in scenario: the lookup line came first, then "On it." and the result.
    said = trim_chatter(
        "On it. I'll check you in 24 hours before the 8:35 departure, so Wednesday at 8:35 AM.",
        said_before=True,
    )
    assert said == "I'll check you in 24 hours before the 8:35 departure, so Wednesday at 8:35 AM."


def test_a_lone_line_and_real_answers_stay() -> None:
    assert trim_chatter("Looking up your Skyward booking.", said_before=False) == (
        "Looking up your Skyward booking."
    )
    answer = "Looking at your screen, the error is on line 4. The loop never ends."
    assert trim_chatter(answer, said_before=True) == answer
    assert trim_chatter("Sure thing, here's the summary: rent is up 3%.", said_before=True) == (
        "Sure thing, here's the summary: rent is up 3%."
    )


def test_commentary_with_then_and_first_also_goes() -> None:
    for line in [
        "I'll look at what's on your desktop first, then sort it into folders.",
        "Let me see what's piled up on your desktop first.",
        "Okay, I'll pull the booking from your email first.",
    ]:
        assert trim_chatter(line, said_before=True) == "", line
    kept = "I'll schedule check-in for Skyward flight SK 482 to Toronto."
    assert trim_chatter(kept, said_before=True) == kept
