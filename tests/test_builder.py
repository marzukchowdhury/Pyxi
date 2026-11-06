"""The 3D builder: the chat model writes a brief, a stronger modeller builds the model."""

from __future__ import annotations

import asyncio

from pyxi.scenes.model_examples import HEART
from test_brain_and_server import make_brain
from test_connectors_and_docs import Script


async def test_a_brief_is_built_in_the_background_and_shown(settings):
    settings.model_3d = "anthropic/claude-sonnet-5.5"
    chat = Script(
        [
            (
                "build_3d_model",
                {"subject": "human heart", "show": "four chambers, blood in and out"},
            ),
            ("say", {"text": "Four rooms and a lot of pumping. Watch this."}),
        ]
    )
    brain, rec = make_brain(settings, llm=chat)
    brain._model_llm = Script([("build_3d_model", HEART)])  # the modeller
    brain._user_text = "show me in 3D how a heart beats"
    names = {t.name: t for t in brain._companion_tools()}
    assert "subject" in names["build_3d_model"].parameters["properties"]  # chat gets the brief form
    await brain.turn("show me in 3D how a heart beats")
    for _ in range(100):
        if rec.of("object"):
            break
        await asyncio.sleep(0.02)
    [obj] = rec.of("object")
    assert obj["scene"]["title"] == "How your heart beats" and obj["restart"]
    assert "human heart" in brain._model_llm.prompts[0]  # the brief reached the modeller
    order = [e["type"] for e in rec.events if e["type"] in ("say", "object")]
    assert order[0] == "say"  # Pyxi talks while the model is built, instead of a silent wait


async def test_without_a_builder_the_chat_model_builds_it_itself(settings):
    settings.model_3d = None
    brain, rec = make_brain(settings, llm=Script([("build_3d_model", HEART)]))
    brain._user_text = "show me a heart in 3D"
    assert (
        "parts"
        in {t.name: t for t in brain._companion_tools()}["build_3d_model"].parameters["properties"]
    )
    await brain.turn("show me a heart in 3D")
    assert rec.of("object")


def test_3d_tools_only_when_asked(settings):
    brain, _ = make_brain(settings)
    brain._user_text = "explain this heart diagram to me"
    names = {t.name for t in brain._companion_tools()}
    assert "explain_on_screen" in names and "build_3d_model" not in names
    brain._user_text = "now show it to me in 3D"
    assert "build_3d_model" in {t.name for t in brain._companion_tools()}
