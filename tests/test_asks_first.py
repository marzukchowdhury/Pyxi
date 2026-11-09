"""One question when it matters, and a first line worth hearing. These are prompt rules: the
tests pin the wording; scripts/scenarios.py ("shop vague table", "shop exact desk") checks the
behaviour against the real model."""

from __future__ import annotations

from pyxi.companion import prompts

SYSTEM = " ".join(prompts.SYSTEM.split())


def test_a_vague_shopping_request_gets_one_question_first() -> None:
    assert "Shopping, recommendations and research" in SYSTEM
    assert "the kind, the size or the budget changes the answer" in SYSTEM
    assert "ask ONE short question before you search, both parts in one sentence" in SYSTEM
    assert "with no tool call in that reply" in SYSTEM


def test_a_specific_request_is_never_asked_about_and_never_twice() -> None:
    assert "Never ask when the request is already specific" in SYSTEM
    assert "Never more than one question" in SYSTEM
    assert "Places near them are not this" in SYSTEM  # those still go straight to Maps


def test_the_starting_line_of_a_long_lookup_says_something_useful() -> None:
    assert "make that one starting line worth hearing" in SYSTEM
    assert "the criterion you're using or what you're comparing" in SYSTEM
    # the quiet rule stays: one line at the start, one at the end
    assert "still one line at the start and one at the end, with no narration in between" in SYSTEM
    assert "say one short starting line and nothing else" in SYSTEM


def test_the_prompt_still_builds() -> None:
    text = prompts.system_prompt("warm", "Scout: browser")  # no stray braces in the new rules
    assert "ONE short question" in text and "Scout: browser" in text
