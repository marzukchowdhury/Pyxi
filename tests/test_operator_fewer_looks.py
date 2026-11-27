"""The desktop hands spend their time asking the model, not looking or clicking.

Measured on 13 November: one look at the screen takes 0.8 s, one answer from the model 4 to 5 s.
Making three heading cells bold and red took nine answers and 52 seconds; opening one thing took
one click and then twenty seconds of the model checking that it had opened. So: more is done per
answer, and an answer that completes the task can say so, with Pyxi itself (no model) checking
that the screen moved.
"""

from __future__ import annotations

from test_operator import Scripted, operator, screen


class Counting(Scripted):
    def __init__(self, *calls) -> None:
        super().__init__(*calls)
        self.asked = 0
        self.systems: list[str] = []
        self.tools: list = []

    async def complete(self, **kw):
        self.asked += 1
        self.systems.append(kw.get("system", ""))
        self.tools = kw.get("tools", [])
        return await super().complete(**kw)


BOLD = {"actions": [{"do": "click", "element_id": 3}, {"do": "type", "element_id": 3, "text": "A1:C1"}, {"do": "press", "text": "enter"}, {"do": "press", "text": "ctrl+b"}]}


async def test_an_act_that_completes_the_task_ends_it_with_no_further_answer():
    box = []
    llm = Counting(("act", {**BOLD, "finish": "The headings are bold."}))
    out = await operator(llm, box).run("make the headings bold")
    assert out.ok and out.summary == "The headings are bold."
    assert llm.asked == 1  # not a second answer to say "done"
    assert [d[0] for d in box[0].did] == ["click", "click", "type", "press", "press"]


async def test_without_finish_the_model_is_asked_again_as_before():
    box = []
    llm = Counting(("act", BOLD), ("done", {"ok": True, "summary": "Bold now."}))
    out = await operator(llm, box).run("make the headings bold")
    assert out.ok and out.summary == "Bold now." and llm.asked == 2


async def test_a_screen_that_did_not_move_is_not_taken_on_trust():
    box = []
    same = screen("white")
    llm = Counting(("act", {**BOLD, "finish": "The headings are bold."}), ("done", {"ok": False, "summary": "Nothing happened."}))
    out = await operator(llm, box, capture=lambda: same).run("make the headings bold")
    assert not out.ok and out.summary == "Nothing happened."  # the model looked, and said so
    assert llm.asked == 2
    assert "did not change" in out.steps[0].note


async def test_finish_is_ignored_when_a_step_of_the_act_did_not_go_through():
    box = []
    typed_into_password = {"actions": [{"do": "type", "element_id": 2, "text": "hello"}], "finish": "Typed it."}
    llm = Counting(("act", typed_into_password), ("done", {"ok": False, "summary": "They need to type that."}))
    out = await operator(llm, box).run("fill the box")
    assert not out.ok and llm.asked == 2  # a skipped step is never "done"


async def test_up_to_eight_actions_go_in_one_act_and_the_model_is_told_why():
    box = []
    many = {"actions": [{"do": "press", "text": k} for k in ("ctrl+a", "ctrl+b", "ctrl+i", "ctrl+u", "ctrl+c", "tab", "tab", "enter", "f2", "f5")], "finish": "Done."}
    llm = Counting(("act", many))
    await operator(llm, box).run("format it all")
    assert [d[1] for d in box[0].did] == ["ctrl+a", "ctrl+b", "ctrl+i", "ctrl+u", "ctrl+c", "tab", "tab", "enter"]  # eight, no more
    system = llm.systems[0]
    assert "1-8 actions" in system and "about five seconds" in system and "ctrl+b" in system
    act = next(t for t in llm.tools if t.name == "act")
    assert "finish" in act.parameters["properties"] and "no further look" in act.parameters["properties"]["finish"]["description"]
