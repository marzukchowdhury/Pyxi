"""The bugs found in review, each pinned so it can't come back."""

from __future__ import annotations

import asyncio

from otto.types import LLMResponse, ToolCall, Usage

from pyxi.companion.brain import visual_opener
from pyxi.companion.errands import run_errand
from pyxi.connectors import Outgoing
from pyxi.screen.ocr import TextLine, find_text
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script
from test_errands import connect, tomorrow_at


def test_a_real_first_sentence_is_never_treated_as_filler():
    text = "Okay, the CPU is the chip under the fan. Watch it spin up. Then more."
    assert visual_opener(text).startswith("Okay, the CPU is the chip under the fan.")


def test_quotes_are_found_in_any_script():
    line = TextLine(
        "Привет мир", (0, 0, 200, 20), [("Привет", (0, 0, 90, 20)), ("мир", (100, 0, 60, 20))]
    )
    assert find_text([line], "мир") == (100, 0, 60, 20)


async def test_a_tool_that_cant_act_is_reported_not_silent(settings):
    # No accounts connected: the errand tool says so, and the model gets a round to tell the user.
    llm = Script(
        [("say", {"text": "On it."}), ("errand", {"task": "Book a meeting tomorrow at 3"})],
        [("say", {"text": "You'll need to connect Google first, in Settings."})],
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("book a meeting tomorrow at 3")
    await asyncio.sleep(0.05)
    assert "connect Google first" in " ".join(s["text"] for s in rec.of("say"))


async def test_pressing_talk_while_thinking_drops_the_old_answer(settings):
    class Slow(Script):
        async def complete(self, **kw):
            await asyncio.sleep(0.2)
            return await super().complete(**kw)

    brain, rec = make_brain(settings, llm=Slow([("say", {"text": "The old answer."})]))
    turn = asyncio.create_task(brain.turn("first question"))
    await asyncio.sleep(0.05)
    brain.interrupt()  # push-to-talk pressed mid-think
    await turn
    await asyncio.sleep(0.05)
    assert not any("old answer" in s["text"] for s in rec.of("say"))


async def test_a_failed_screen_prefetch_doesnt_break_the_next_turn(settings):
    brain, rec = make_brain(settings, llm=Script([("say", {"text": "Fine."})]))

    async def boom():
        raise RuntimeError("capture failed")

    brain._prefetch = asyncio.ensure_future(boom())
    await brain.turn("hello")
    assert [s["text"] for s in rec.of("say")] == ["Fine."]
    assert brain._prefetch is None


async def test_a_broken_streamed_call_is_asked_for_again(settings):
    class Broken(Script):
        async def complete(self, **kw):
            if not hasattr(self, "once"):
                self.once = True
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id="1", name="sketch_diagram", arguments={"__unparseable__": True})
                    ],
                    usage=Usage(),
                    stop_reason="tool_use",
                )
            return await super().complete(**kw)

    brain, rec = make_brain(settings, llm=Broken([("say", {"text": "Here it is."})]))
    await brain.turn("draw it")
    assert any(
        "TOOL_INVALID" in n for n in brain.llm.prompts[0:1] + brain.llm.prompts
    )  # asked again
    assert rec.of("say")


async def test_a_routine_never_counts_as_the_user_naming_an_address(settings):
    brain, _ = make_brain(
        settings,
        llm=Script(
            [
                ("errand", {"task": "Invite alice@example.com to standup"}),
                ("say", {"text": "On it."}),
            ]
        ),
    )
    connect(brain)
    seen: list[str] = []

    async def fake(task: str, user_text: str, **kw) -> None:
        seen.append(user_text)

    brain._run_errand = fake  # type: ignore[method-assign]
    await brain.turn("(Routine: invite alice@example.com to standup)", synthetic=True)
    await asyncio.sleep(0.05)
    assert seen == [""]  # so the invite will need approval


async def test_an_errand_never_sends_twice_without_asking(settings):
    start = tomorrow_at(15).strftime("%Y-%m-%dT%H:%M")
    asked: list[str] = []
    runs: list[int] = []

    def invite(n: int) -> Outgoing:
        async def run() -> str:
            runs.append(n)
            return f"CREATED event {n}"

        return Outgoing(
            action=f"Invite bob ({n})", preview="", run=run, kind="invite", recipients=["bob@x.com"]
        )

    class T:
        def __init__(self) -> None:
            from otto.llm.base import ToolSpec

            self.spec = ToolSpec("calendar_add", "", {"type": "object", "properties": {}})
            self.n = 0

        async def handler(self, args):
            self.n += 1
            return invite(self.n)

    llm = Script(
        [("calendar_add", {"start": start})],
        [("calendar_add", {"start": start})],  # "fixing" it by booking again
        [("finish", {"ok": True, "title": "Set", "summary": "Done."})],
    )

    async def approve(out: Outgoing) -> bool:
        asked.append(out.action)
        return False

    await run_errand(
        task="Meet bob@x.com tomorrow at 3",
        user_text="meet bob@x.com tomorrow at 3",
        now="Now: ...",
        llm=llm,
        tools=[T()],
        approve=approve,
    )
    assert runs == [1]  # the first went ahead (the user named bob)
    assert asked == ["Invite bob (2)"]  # the second had to ask, and was declined
