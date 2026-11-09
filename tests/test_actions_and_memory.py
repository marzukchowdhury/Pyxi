"""Quick actions (open, copy, insert) and memory you can ask about or clear."""

from __future__ import annotations

from conftest import fake_context
from pyxi.companion.actions import app_target, safe_url
from test_brain_and_server import make_brain
from test_notes_and_guides import Scripted


def test_only_web_addresses_and_known_apps():
    assert safe_url("example.com/docs") == "https://example.com/docs"
    assert safe_url("http://x.org") == "http://x.org"
    assert safe_url("file:///C:/Windows") is None and safe_url("javascript:alert(1)") is None
    assert (
        app_target("Calculator") == "calc.exe"
        and app_target("sound settings") == "ms-settings:sound"
    )
    assert app_target("rm -rf") is None


async def test_open_copy_and_insert_go_to_the_desktop_app(settings):
    llm = Scripted(
        [
            ("quick_action", {"action": "open_url", "value": "example.com"}),
            ("quick_action", {"action": "copy", "value": "ABC-123"}),
            ("quick_action", {"action": "insert", "value": "Thanks, see you at 3!"}),
            ("quick_action", {"action": "open_url", "value": "javascript:alert(1)"}),
            ("say", {"text": "Done."}),
        ]
    )
    brain, rec = make_brain(settings, llm=llm)
    await brain.turn("reply to this for me")
    acts = [(a["kind"], a["value"]) for a in rec.of("action")]
    assert acts == [
        ("open_url", "https://example.com"),
        ("copy", "ABC-123"),
        ("insert", "Thanks, see you at 3!"),
    ]


async def test_insert_brings_the_named_app_forward_or_copies(settings, monkeypatch):
    """Live: "write a packing list in Notepad" was pasted into the calculator, which had the focus."""
    from pyxi import windows

    focused: list[int] = []
    front = {"hwnd": 0}
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "open_windows", lambda minimized=False: [(4, "Untitled - Notepad", "notepad.exe"), (5, "Calculator", "calc.exe")])
    monkeypatch.setattr(windows, "focus", lambda hwnd: focused.append(hwnd) or front.update(hwnd=hwnd) or True)
    monkeypatch.setattr(windows, "foreground", lambda: front["hwnd"])
    llm = Scripted(
        [("quick_action", {"action": "insert", "value": "socks"}), ("say", {"text": "Ok."})]
    )
    brain, rec = make_brain(settings, llm=llm)
    brain._named_window = lambda text, ctx: 4 if "notepad" in text.lower() and ctx is None else None  # type: ignore[method-assign]
    await brain.turn("write socks in Notepad")
    assert focused and focused[0] == 4
    assert [a["kind"] for a in rec.of("action")] == ["insert"]
    # Windows won't hand over the focus: copied, and they're told to paste
    monkeypatch.setattr(windows, "focus", lambda hwnd: False)
    front["hwnd"] = 5
    llm2 = Scripted(
        [("quick_action", {"action": "insert", "value": "socks"}), ("say", {"text": "Ok."})]
    )
    brain2, rec2 = make_brain(settings, llm=llm2)
    brain2._named_window = lambda text, ctx: 4 if ctx is None else None  # type: ignore[method-assign]
    await brain2.turn("write socks in Notepad")
    assert [a["kind"] for a in rec2.of("action")] == ["copy"]


async def test_what_do_you_know_and_forget(settings):
    llm = Scripted(
        [("remember", {"key": "name", "content": "Marzuk"}), ("say", {"text": "Noted."})],
        [("recall_memories", {})],
        [("say", {"text": "You're Marzuk."})],
        [("forget", {"everything": True}), ("say", {"text": "Gone."})],
    )
    brain, _ = make_brain(settings, llm=llm)
    await brain.turn("call me Marzuk")
    await brain.turn("what do you know about me?")
    assert "- name: Marzuk" in llm.prompts[2]  # the lookup came back to the model
    await brain.turn("forget everything about me")
    assert brain.memory.all("agent:pyxi") == []
