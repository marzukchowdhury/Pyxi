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


def test_on_it_becomes_a_different_acknowledgement_each_time() -> None:
    from pyxi.companion.brain import ACKS, vary_ack

    recent: list[str] = []
    said = [vary_ack("On it.", recent) for _ in range(8)]
    assert all(s.rstrip(".") in ACKS for s in said)
    # never the same as any of the few before it
    for i, s in enumerate(said):
        assert s not in said[max(0, i - 4) : i], said
    # the rest of the line stays
    line = vary_ack("On it, I'll let you know when it's ready.", recent)
    assert line.endswith(", I'll let you know when it's ready.") and line.split(",")[0] in ACKS


def test_only_a_leading_acknowledgement_is_swapped() -> None:
    from pyxi.companion.brain import vary_ack

    for text in ["Online banking is safer than it looks.", "It's on it's way.", "Kolkata is on it."]:
        assert vary_ack(text, []) == text
