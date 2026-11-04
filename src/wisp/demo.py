"""Demo mode: a rule-based stand-in for the model, so anyone can try Wisp with no API key.

It understands a handful of requests ("how does HTTPS work?", "explode this panel",
"where is the close button?", "have Scout fill in the demo form", "show me how you did it")
and answers with real tool calls, so everything downstream (speech, pointing, 3D scenes,
jobs, walkthroughs) is the genuine system. For real conversations use Claude, or local
mode with Ollama.
"""

from __future__ import annotations

import itertools
import re
import uuid

from otto.llm.base import Message, TextPart, ToolSpec
from otto.types import LLMResponse, ToolCall, Usage

_ELEMENT = re.compile(r'\[(\d+)\] (\w+)(?: "([^"]*)")?')


def _call(tool: str, /, **args: object) -> ToolCall:
    return ToolCall(id="demo_" + uuid.uuid4().hex[:8], name=tool, arguments=args)


class DemoLLM:
    model = "demo"

    def __init__(self, demo_site_url: str | None = None) -> None:
        self.demo_site_url = demo_site_url

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        prompt = "\n".join(p.text for m in messages for p in m.parts if isinstance(p, TextPart))
        user = prompt.rsplit("# The user says", 1)[-1].strip().lower()
        if "# results from your lookups" in prompt.lower():
            lookups = prompt.rsplit("# Results from your lookups this turn", 1)[-1]
            ids = [int(i) for i in re.findall(r"#(\d+) \d\d:\d\d", lookups)][-6:]
            if ids:
                links = [
                    {"from": a, "to": b, "why": "that's what you did next"}
                    for a, b in itertools.pairwise(ids)
                ]
                return LLMResponse(
                    tool_calls=[
                        _call("say", text="Here's the chain of events that got you here."),
                        _call(
                            "causal_replay",
                            title="How you got here",
                            summary="Each glowing thread is one step leading to the next.",
                            event_ids=ids,
                            links=links,
                        ),
                    ],
                    usage=Usage(),
                )
            return LLMResponse(
                tool_calls=[_call("say", text="Here's what I found in my memory of your screen.")],
                usage=Usage(),
            )
        elements = [
            (int(i), role, name or "")
            for i, role, name in _ELEMENT.findall(prompt.split("# The user says")[0])
        ]
        return LLMResponse(
            tool_calls=self._route(user, elements),
            usage=Usage(input_tokens=1, output_tokens=1),
            model=self.model,
        )

    def _route(self, user: str, elements: list[tuple[int, str, str]]) -> list[ToolCall]:
        def has(*words: str) -> bool:
            return any(w in user for w in words)

        if has("https", "padlock", "secure", "encrypt"):
            return [
                _call("say", text="Ooh, good one. Let me build it for you in 3D."),
                _call("show_scene", name="https"),
            ]
        if has("eject", "pilot", "fighter jet"):
            return [
                _call("say", text="Strap in. This one's got rockets."),
                _call("show_scene", name="ejection_seat"),
            ]
        if has("engine", "piston", "four-stroke", "four stroke"):
            return [
                _call("say", text="Suck, squeeze, bang, blow. Watch the piston."),
                _call("show_scene", name="engine"),
            ]
        if has("how do you work", "how does wisp", "how you work", "who are you"):
            return [
                _call("say", text="Here's a peek at my insides!"),
                _call("show_scene", name="wisp"),
            ]
        if has("neural", "machine learning", "network learn"):
            return [
                _call("say", text="Picture it as layers passing numbers forward."),
                _call("show_scene", name="neural_net"),
            ]
        if has("git", "branch", "merge"):
            return [
                _call("say", text="Branches are easier to see than to explain."),
                _call("show_scene", name="git"),
            ]
        if has("how did i get here", "what happened", "what did i do", "how did this happen"):
            return [_call("say", text="Let me rewind a little."), _call("get_activity", minutes=60)]
        if has("take me back", "time travel", "go back to", "what did it look like"):
            return [
                _call("say", text="Hold on, travelling back in time."),
                _call("time_travel", query=user),
            ]
        if has("explode", "what is all this", "break this down", "how is this built"):
            containers = [
                e for e in elements if e[1] in {"Pane", "Group", "ToolBar", "Tab", "List"}
            ]
            if containers:
                target = max(containers, key=lambda e: sum(1 for x in elements if x[0] > e[0]))
                return [
                    _call("say", text="Let me pull this apart so you can see the layers."),
                    _call("explode_view", element_id=target[0], title="How this window is built"),
                ]
            return [
                _call(
                    "say",
                    text="I can't see a panel to pull apart here. Try it on a settings window!",
                )
            ]
        if has("walk me through", "show me how", "how did you do", "teach me"):
            return [
                _call("say", text="Sure, here's every step I took."),
                _call("start_walkthrough"),
            ]
        if has("scout", "fill", "form", "research", "application"):
            url = self.demo_site_url or "http://127.0.0.1:8765"
            return [
                _call(
                    "say",
                    text="On it! Scout is heading to the demo site now. You can watch in the panel.",
                ),
                _call(
                    "delegate",
                    agent="scout",
                    task="Fill the partner application on the demo site and stop on the review page.",
                    start_url=url,
                ),
            ]
        if has("where", "point", "find", "show me the", "which"):
            words = [
                w
                for w in re.findall(r"[a-z]{3,}", user)
                if w not in {"where", "the", "point", "find", "show", "which", "button", "is"}
            ]
            for eid, _, name in elements:
                if name and any(w in name.lower() for w in words):
                    return [_call("say", text=f"Right here! [point:{eid}]")]
            return [_call("say", text="Hmm, I don't see that on screen right now.")]
        return [
            _call(
                "say",
                text="I'm in demo mode, so I know a few tricks: ask how HTTPS works, how I work, to explode "
                "a panel, where a button is, or have Scout fill in the demo form.",
            )
        ]
