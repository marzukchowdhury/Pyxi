"""The companion's tools: speak, point, draw, explain in 3D, remember, and delegate."""

from __future__ import annotations

from otto.llm.base import ToolSpec

from wisp.scenes import TITLES, tool_schema
from wisp.scenes.object_gallery import OBJECT_TITLES
from wisp.scenes.objects import build_tool_schema, edit_tool_schema
from wisp.scenes.sketch import SKETCH_TOOL_SCHEMA

_QUOTE = {
    "quote": {
        "type": "string",
        "description": "The exact words on screen this is about, copied from the OCR text list (e.g. "
        "'Iran War'). Wisp finds those words and marks them precisely; best for text in images.",
    }
}
_TARGET = {
    "element_id": {
        "type": "integer",
        "description": "An [id] from the UI element list (preferred: exact).",
    },
    "x": {"type": "number", "description": "Screenshot x in px, if there's no element id."},
    "y": {"type": "number", "description": "Screenshot y in px."},
    **_QUOTE,
}
_BOX = {
    "element_id": _TARGET["element_id"],
    "box": {
        "type": "array",
        "items": {"type": "number"},
        "description": "[x, y, w, h] in screenshot px, if no element id.",
    },
    **_QUOTE,
}
_LABEL = {
    "label": {"type": "string", "description": "Short caption next to the drawing (max ~6 words)."}
}


def _tool(name: str, description: str, props: dict, required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(
        name, description, {"type": "object", "properties": props, "required": required or []}
    )


SAY = _tool(
    "say",
    "Say something out loud to the user. Call exactly once per turn (twice only if you also look something up). "
    "Keep it short and conversational. You can sync drawings with speech by putting markers right where they "
    "belong: [point:12], [circle:12] or [highlight:12] (UI element or OCR line ids). Optional sound tags: [laugh], [chuckle], [sigh].",
    {"text": {"type": "string"}},
    ["text"],
)

# When a dedicated 3D builder is configured, the chat model only describes what to build (a few
# lines) and a stronger modeller builds it: better shapes, and the conversation stays quick.
BUILD_BRIEF = _tool(
    "build_3d_model",
    "Have a real-looking 3D model built and shown floating on screen, with sticky-note callouts and "
    "narrated, animated beats (about 15 seconds to build). Use it for 'draw it in 3D', 'show me inside X', "
    "'show me how to attach/assemble X', for any physical thing (a jet, a heart, a lock, a camera). "
    "Describe exactly what to build; in the same reply, `say` one short line while it builds (the verdict "
    "and a hook), and never describe the model's parts in speech: its own narration does that.",
    {
        "subject": {
            "type": "string",
            "description": "Exactly what it is, as on their screen: 'F-35B fighter jet', 'human heart'.",
        },
        "show": {
            "type": "string",
            "description": "What to reveal and explain: the parts that matter, what moves, what flows "
            "where, inside or outside, any step-by-step action (e.g. 'lift the latch, slide the cable in').",
        },
        "beats": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 step ideas in order, a few words each.",
        },
    },
    ["subject", "show"],
)

COMPANION_TOOLS: list[ToolSpec] = [
    SAY,
    _tool("point", "Fly over and point at something on screen.", {**_TARGET, **_LABEL}),
    _tool("circle", "Draw a hand-drawn circle around something.", {**_BOX, **_LABEL}),
    _tool("highlight", "Draw a glowing box around an area.", {**_BOX, **_LABEL}),
    _tool(
        "arrow",
        "Draw an arrow from one thing to another (e.g. 'drag this here').",
        {
            "from_element_id": {"type": "integer"},
            "from_xy": {"type": "array", "items": {"type": "number"}},
            "to_element_id": {"type": "integer"},
            "to_xy": {"type": "array", "items": {"type": "number"}},
            **_LABEL,
        },
    ),
    _tool("clear_drawings", "Remove all drawings from the screen.", {}),
    _tool(
        "explain_on_screen",
        "Explain something that's on screen by writing on it: a note card beside the content whose points "
        "write themselves as you speak, each framing the exact paragraph, row or control it's about, plus "
        "freehand sketches over real content. Use it whenever they ask what's on screen, what something "
        "says or means, or to explain a post, meme, chart, page or error. Be thorough: 4-6 points that "
        "together really answer the question (what it is, the why, the catch, what it means for them), each "
        "card text under 10 words; an 'aside' point can carry a joke. Each point frames its "
        "own specific thing (one cell, one line, one control): never reuse the same frame. Give `quote` (exact "
        "words from the OCR list) to mark words precisely, with mark 'underline' for a phrase and 'frame' for "
        "a block; the card places itself where it hides nothing. Give every point a `say`: the one sentence you "
        "speak as it appears (its mark draws at that moment), explaining it in your voice, not reading the card. "
        "Your separate `say` call is only the opener (the verdict and a hook, no [note:N] markers); Wisp then "
        "talks through the points in order. Answer their actual question in the points (the why, the catch). "
        "Finish with `closing`, so the explanation lands instead of stopping. "
        "PICTURES: when they ask about a diagram, photo, drawing, map or illustration on screen (a heart, an "
        "engine, a circuit, a painting), use style 'on_picture' and give `picture` (its box) FIRST: no card, "
        "the explanation goes onto the picture itself. Each point's `text` becomes a small tag (1-3 words, "
        "e.g. 'right ventricle', 'toward lungs') pinned to its `part` (the part's usual name; Wisp finds it "
        "precisely), and `flow` traces a route through named parts as you say it (blood, air, current, a "
        "signal): give a `flow` on every point where something moves, so the route draws itself across "
        "the picture step by step. Walk it in the order things happen. Never make a separate diagram for this: sketch_diagram "
        "is only for when they ask you to draw one.",
        {
            "style": {
                "type": "string",
                "enum": ["card", "on_picture"],
                "description": "on_picture to explain a picture by tagging it; card for everything else.",
            },
            "picture": {
                "type": "array",
                "items": {"type": "number"},
                "description": "on_picture: [x, y, w, h] screenshot px of the whole picture.",
            },
            "picture_element_id": {
                "type": "integer",
                "description": "on_picture: the picture's Image element id, if it has one.",
            },
            "title": {"type": "string", "description": "2-4 words, e.g. 'Why this is funny'."},
            "closing": {
                "type": "string",
                "description": "Said after the last point: one related surprising or funny fact (or the "
                "'so that's why'), then ONE specific offer of what to show next, e.g. 'Want me to sketch how "
                "the money burns, or a fun fact about what training one model costs?'",
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The card's short version, under 10 words.",
                        },
                        "say": {
                            "type": "string",
                            "description": "One spoken sentence for this point, said as its mark draws.",
                        },
                        "kind": {"type": "string", "enum": ["point", "step", "formula", "aside"]},
                        "part": {
                            "type": "string",
                            "description": "on_picture: the part this tag pins to, e.g. 'right atrium'.",
                        },
                        "at": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "on_picture: your rough [x, y] of the part, screenshot px.",
                        },
                        "flow": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "on_picture: 3-5 parts a route passes through in order, "
                            "including the valves or vessels on the way so the line follows the real "
                            "path, traced as this point is said, e.g. ['superior vena cava', 'right "
                            "atrium', 'tricuspid valve', 'right ventricle'].",
                        },
                        "quote": _QUOTE["quote"],
                        "element_id": {
                            "type": "integer",
                            "description": "What this point is about: a UI element or OCR line id.",
                        },
                        "box": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "[x, y, w, h] screenshot px if no id.",
                        },
                        "mark": {
                            "type": "string",
                            "enum": ["frame", "underline", "circle", "none"],
                        },
                    },
                    "required": ["text", "say"],
                },
            },
            "sketches": {
                "type": "array",
                "description": "Freehand marks: path (a stroke through points, e.g. a chart's rise), arrow (first to last point), "
                "label (handwritten text at a point), tick, cross.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["path", "arrow", "label", "tick", "cross"],
                        },
                        "points": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "number"}},
                            "description": "Screenshot px [[x, y], ...].",
                        },
                        "element_id": {"type": "integer"},
                        "quote": _QUOTE["quote"],
                        "text": {"type": "string"},
                        "after": {
                            "type": "integer",
                            "description": "Appear with note point N (0 = immediately).",
                        },
                    },
                },
            },
        },
        ["title", "items", "closing"],
    ),
    _tool(
        "guide",
        "Walk the user through doing something themselves, one click at a time: mark the single control to click "
        "next and say what to do. Wisp waits for their real click on it, then you see the new screen and give the "
        "next step (call guide again), or say they're done (done=true).",
        {
            "element_id": {"type": "integer"},
            "box": {"type": "array", "items": {"type": "number"}},
            "label": {"type": "string", "description": "What to click, 1-4 words, e.g. 'Export'."},
            "goal": {
                "type": "string",
                "description": "The overall goal, e.g. 'turn on dark mode'.",
            },
            "done": {
                "type": "boolean",
                "description": "True when the goal is reached (no new target).",
            },
        },
        ["goal"],
    ),
    ToolSpec(
        "explain_in_3d",
        "Build an interactive 3D explanation that floats over the screen. Use it when the user is confused, asks how "
        "something works, or a structure/flow/process is easier to show than to say. Prefer 4-10 nodes and 3-6 steps, "
        "each step with one or two short narration sentences. Layouts: flow (pipelines, request/response), tree "
        "(hierarchies), orbit (a core with parts), stack (layers), grid (matrices, networks), timeline (sequences). "
        "Anchor it to the element it explains.",
        tool_schema(),
    ),
    ToolSpec(
        "sketch_diagram",
        "Draw a hand-made diagram that inks itself in while you talk: the fastest, clearest way to explain a "
        "mechanism, force, flow, process or comparison ('is this real?', 'how does it hover?', 'why does it "
        "float?', 'how does the signal get there?'). Grid is 100 wide x 64 tall, y down. Draw the subject simply "
        "(a side view outline as one polygon or curve, its key parts as rects/ellipses), then what acts on it "
        "(arrows; flow=true when something moves: air, water, current, data), then short labels (text) next to "
        "what they name, and finish with the takeaway as an `equation` or rule ('Lift = weight'). Each shape has "
        "a `step`; give 2-5 steps, each one short spoken sentence, so the drawing builds with the words. Colours: "
        "ink (outlines), ice (the subject's key part), amber (the main force/result), rose (problem/heat), mint "
        "(good/flow), violet, muted (secondary). Labels 1-3 words; never let text overlap a shape. Set `anchor` to "
        "the thing on screen it explains (video, photo) so a thread joins them. Example, a hovering jet: polygon "
        "[[8,34],[22,29],[66,29],[80,20],[86,20],[84,31],[92,34],[84,38],[22,38]] ink fill step 0; rect at "
        "[34,30] size [8,7] ice step 0; text 'Lift fan' at [30,25] step 0; arrow [[38,40],[38,56]] ice flow step "
        "1; text 'Air down' at [26,61] step 1; arrow [[38,20],[38,6]] amber bold step 2; text 'Lift' at [41,10] "
        "step 2; arrow [[58,40],[58,52]] muted step 2; text 'Weight' at [61,50] step 2; equation 'Lift = weight' "
        "at [58,12] step 3.",
        SKETCH_TOOL_SCHEMA,
    ),
    ToolSpec(
        "build_3d_model",
        "Build a real-looking 3D model of a physical thing (machine, vehicle, device, organ, building) from parts, "
        "with sticky-note callouts and narrated, animated beats that play like a short video. It floats on the "
        "screen with no background, so the object itself must look right and fill the frame. Use it for 'draw it "
        "in 3D', 'show me inside X', 'show me how to attach/assemble X'; use sketch_diagram for quick 'is this "
        "real / why' answers and explain_in_3d for ideas. Units: y is up, the whole model 6-12 units across, centred "
        "near the origin. The same rules for anything (a jet, a heart, a lock, a camera, a phone, an engine). "
        "Build shapes the way they really look: any flat outline (a fuselage, hull, wing, fin, blade, circuit "
        "board, phone body, key) is ONE extrude with its real outline as `profile`, never stacked boxes; long "
        "round parts are cylinders with `axis` set; fans and turbines are gears with many teeth; soft organic "
        "parts (heart chambers, lungs) are overlapping spheres/capsules, vessels and pipes are tubes along a "
        "`path`. Show what moves and flows: every fan, rotor, wheel or gear gets a spin motion; a beating, "
        "breathing or pumping part gets an oscillate on scale (a heart: ventricles squeeze every 0.8 s); a "
        "latch, trigger, piston or slide gets keyframes; whatever travels (air, blood, water, fuel, current, "
        "data, force) gets `arrow` parts along its route (path [from, ..., to]; red/blue for oxygen-rich/poor "
        "blood) which glow and glide by themselves. Label the 4-7 parts that matter with 1-3 words each and no `note`. Outer shells get "
        "casing=true and cutaway=true when the inside is the point (the shell stays as a see-through ghost). Group "
        "assemblies with `parent`. Give 3-5 beats with rising `at` times, each focusing the parts it talks about; "
        "keyframes on the story timeline for one-off events (a latch lifting, a cable sliding in). Cameras: side "
        "for vehicles and profiles, iso to show depth, close for a detail.",
        build_tool_schema(),
    ),
    ToolSpec(
        "edit_3d_model",
        "Change the 3D model already on screen: 'show the inside' (cutaway), 'remove the casing' (hide), "
        "'make that part move' (add_motions), recolour or resize parts, add parts, or replace the beats with a new "
        "explanation. Refer to parts by id.",
        edit_tool_schema(),
    ),
    _tool(
        "explode_view",
        "Peel a real panel/dialog/toolbar on screen apart into a 3D exploded hierarchy, built from crops of the "
        "actual UI. Best for 'what is all this?' about a complex panel.",
        {
            "element_id": {"type": "integer", "description": "The container element to explode."},
            "title": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "narration": {"type": "string"},
                        "focus_element_ids": {"type": "array", "items": {"type": "integer"}},
                    },
                },
            },
        },
        ["element_id"],
    ),
    _tool(
        "show_scene",
        "Play a ready-made 3D scene from the gallery.",
        {
            "name": {
                "type": "string",
                "enum": [*TITLES, *OBJECT_TITLES],
                "description": "; ".join(
                    f"{k}: {v}" for k, v in {**TITLES, **OBJECT_TITLES}.items()
                ),
            }
        },
        ["name"],
    ),
    _tool(
        "delegate",
        "Hand a task to a teammate who works in the background. Browser agents (like Scout) drive a real browser; "
        "writers (like Quill) draft text; explainers (like Sketch) build 3D scenes. Chain a handoff with then_agent/"
        "then_task, e.g. Scout researches then Quill drafts. Give complete, specific instructions.",
        {
            "agent": {"type": "string"},
            "task": {"type": "string"},
            "start_url": {"type": "string"},
            "then_agent": {"type": "string"},
            "then_task": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths of documents on this PC for a writer to fix (.docx, .txt, .md, .pdf); "
                "the fixed copy is saved beside the original.",
            },
        },
        ["agent", "task"],
    ),
    _tool(
        "web_search",
        "Search the web (about a second). Use it for anything that changes or that you're not sure of: "
        "news, prices, scores, results, weather, release dates, who runs what, whether a claim or quote is "
        "real, facts about a specific person, product or place. Searches the web and the news together, and "
        "news comes with its outlet and age. In the reply where you search, say at most a few words ('Checking "
        "the news.'), never the answer; answer once the results are back, naming the sources. One search is "
        "usually enough; two at most. Never say you're searching unless you call this tool.",
        {
            "query": {"type": "string", "description": "What you'd type into a search box."},
        },
        ["query"],
    ),
    _tool(
        "read_page",
        "Read the text of one web page (a search result, or a link on screen) when the snippet isn't enough.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _tool(
        "recall_screen",
        "Search your spatial memory of earlier screens ('where was the export button?').",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "look_closer", "Zoom into part of the screen for a sharper look (small text, icons).", _BOX
    ),
    _tool(
        "remember",
        "Save a fact about the user or their work for future conversations.",
        {"key": {"type": "string"}, "content": {"type": "string"}},
        ["key", "content"],
    ),
    _tool(
        "start_walkthrough",
        "Replay a finished browser job as a ghost walkthrough that teaches the user the steps.",
        {"job_id": {"type": "string", "description": "Omit for the most recent browser job."}},
    ),
    _tool(
        "create_agent",
        "Create a new named teammate when the user asks for one.",
        {
            "name": {"type": "string"},
            "role": {"type": "string", "enum": ["browser", "writer", "explainer"]},
            "persona": {"type": "string"},
            "voice": {
                "type": "string",
                "description": "Kokoro voice id, e.g. af_bella, am_adam, bf_emma.",
            },
        },
        ["name", "role", "persona"],
    ),
]

COMPANION_TOOLS += [
    _tool(
        "errand",
        "Do something in the background with the user's connected accounts: set up a meeting or "
        "invite, write or reply to an email, create a doc, post to Discord or LinkedIn. You say one short "
        "line first ('On it, I'll let you know.') and the user gets a notification in the notch when it's "
        "done. Write the task completely and exactly: every name, email address, date and time as the "
        "user said them. If something essential is missing (like when the meeting is), ask the user "
        "first instead.",
        {"task": {"type": "string"}},
        ["task"],
    ),
    _tool(
        "quick_action",
        "Do a small thing for the user right now: open_url (a website), open_app (notepad, calculator, chrome, "
        "word, excel, spotify, discord, settings, 'sound settings', 'microphone privacy'...), copy (exact text to "
        "the clipboard), or insert (type a drafted message into the text field they're in, e.g. 'write a reply "
        "to this'). Only when they asked for it.",
        {
            "action": {"type": "string", "enum": ["open_url", "open_app", "copy", "insert"]},
            "value": {"type": "string", "description": "The URL, app name, or text."},
        },
        ["action", "value"],
    ),
    _tool(
        "arrange_windows",
        "Arrange their windows, right now: split the screen (the window they're in on one half, a site or app "
        "in a new window on the other), snap the window they're in left, right, top or bottom, or maximize, "
        "minimize or restore it. Just do it when asked; never tell them to do it themselves.",
        {
            "layout": {
                "type": "string",
                "enum": [
                    "split",
                    "left",
                    "right",
                    "top",
                    "bottom",
                    "maximize",
                    "minimize",
                    "restore",
                ],
            },
            "open": {
                "type": "string",
                "description": "For split: the website (e.g. https://sheets.google.com) or app (excel, notepad) "
                "for the other half.",
            },
            "current_side": {
                "type": "string",
                "enum": ["left", "right"],
                "description": "For split: where the window they're in goes (default left).",
            },
        },
        ["layout"],
    ),
    _tool(
        "schedule_routine",
        "Set up something Wisp does on a schedule while it's running, e.g. 'every weekday at 9 check my email for "
        "anything from my professor and tell me'. Write `task` as the instruction you'll get at that time.",
        {
            "task": {"type": "string"},
            "days": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "every day",
                        "weekdays",
                        "weekends",
                        "mon",
                        "tue",
                        "wed",
                        "thu",
                        "fri",
                        "sat",
                        "sun",
                    ],
                },
            },
            "time": {"type": "string", "description": "Local time like '09:00' or '6pm'."},
            "every_minutes": {
                "type": "integer",
                "description": "Instead of a time: repeat every N minutes (15 or more).",
            },
        },
        ["task"],
    ),
    _tool("list_routines", "List the user's routines.", {}),
    _tool(
        "cancel_routine",
        "Delete a routine by id or by a word from its task.",
        {"routine": {"type": "string"}},
        ["routine"],
    ),
    _tool(
        "recall_memories",
        "List what you remember about the user (for 'what do you know about me?'), optionally filtered.",
        {"query": {"type": "string"}},
    ),
    _tool(
        "forget",
        "Forget something you remembered about the user (by its key), or everything if they ask.",
        {"key": {"type": "string"}, "everything": {"type": "boolean"}},
    ),
    _tool(
        "get_activity",
        "Read the user's recent activity timeline (window switches, what they clicked or typed into, "
        "what they asked you). Use before causal_replay.",
        {"minutes": {"type": "number", "description": "Default 30."}},
    ),
    _tool(
        "time_travel",
        "Interface time travel: rebuild how the screen looked at some past moment as an explorable 3D hologram with a "
        "timeline scrubber. Use for 'take me back to...', 'what did X look like before...', 'show me when I was...'.",
        {
            "query": {
                "type": "string",
                "description": "What they were looking at, e.g. 'pricing page Figma'.",
            },
            "when": {
                "type": "string",
                "description": "Time words, e.g. 'last Tuesday afternoon', '20 minutes ago'.",
            },
        },
        ["query"],
    ),
    _tool(
        "causal_replay",
        "Causal explanation mode: show the chain of events that led to the current state as a 3D timeline, with "
        "'led to' threads and optional what-if branches. Get event ids with get_activity first; pick only the events "
        "that matter (3-8), and say why each led to the next.",
        {
            "title": {"type": "string"},
            "summary": {"type": "string", "description": "One or two spoken sentences."},
            "event_ids": {"type": "array", "items": {"type": "integer"}},
            "links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "integer"},
                        "to": {"type": "integer"},
                        "why": {"type": "string"},
                    },
                },
            },
            "what_ifs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "after_event_id": {"type": "integer"},
                        "action": {"type": "string"},
                        "outcome": {"type": "string"},
                    },
                },
            },
        },
        ["title", "summary", "event_ids"],
    ),
    _tool(
        "pin_note",
        "Pin a sticky note to an element; it reappears on that element whenever this app is open.",
        {"element_id": {"type": "integer"}, "note": {"type": "string"}},
        ["element_id", "note"],
    ),
]

INFO_TOOLS = {
    "web_search",
    "read_page",
    "list_routines",
    "recall_memories",
    "recall_screen",
    "look_closer",
    "get_activity",
}  # results come back to the model for another round
