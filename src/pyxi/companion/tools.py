"""The companion's tools: speak, point, draw, explain in 3D, remember, and delegate."""

from __future__ import annotations

from otto.llm.base import ToolSpec

from pyxi.agents.pixel import OPERATIONS as PIXEL_OPERATIONS
from pyxi.companion.notes import DIAGRAM_SCHEMA
from pyxi.scenes import TITLES, tool_schema
from pyxi.scenes import library as model_library
from pyxi.scenes.object_gallery import OBJECT_TITLES
from pyxi.scenes.objects import build_tool_schema, edit_tool_schema
from pyxi.scenes.sketch import SKETCH_TOOL_SCHEMA

_QUOTE = {
    "quote": {
        "type": "string",
        "description": "The exact words on screen this is about, copied from the OCR text list (e.g. "
        "'Iran War'). Pyxi finds those words and marks them precisely; best for text in images.",
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
        **(
            {
                "library": {
                    "type": "string",
                    "enum": list(model_library.models()),
                    "description": "A real, ready-made 3D model of exactly this subject (photo-real, "
                    "far better than one built from shapes, and on screen in a second): give its id "
                    "whenever the subject is one of these, or close enough to explain on (any car, "
                    "of any brand or model, BMW, Ferrari, Supra, a sedan: concept_car): "
                    + model_library.menu(),
                }
            }
            if model_library.models()
            else {}
        ),
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
        "says or means, or to explain a post, meme, chart, page or error. Be brief: 3-4 points (3 for "
        "something simple like a meme or a post) that together really answer the question (what it is, the "
        "why, the catch, what it means for them), each "
        "card text under 10 words; an 'aside' point can carry a joke. Each point frames its "
        "own specific thing (one cell, one line, one control): never reuse the same frame. Give `quote` (exact "
        "words from the OCR list) to mark words precisely, with mark 'underline' for a phrase and 'frame' for "
        "a block; the card places itself where it hides nothing. Give every point a `say`: one short sentence "
        "(under 15 words) you speak as it appears (its mark draws at that moment), in your voice, not reading "
        "the card. "
        "Your separate `say` call is only the opener (the verdict and a hook, no [note:N] markers); Pyxi then "
        "talks through the points in order. Answer their actual question in the points (the why, the catch). "
        "Finish with `closing`, so the explanation lands instead of stopping. "
        "PICTURES: when they ask about a diagram, photo, drawing, map or illustration on screen (a heart, an "
        "engine, a circuit, a painting), use style 'on_picture' and give `picture` (its box) FIRST: no card, "
        "the explanation goes onto the picture itself. Each point's `text` becomes a small tag (1-3 words, "
        "e.g. 'right ventricle', 'toward lungs') pinned to its `part` (the part's usual name; Pyxi finds it "
        "precisely), and `flow` traces a route through named parts as you say it (blood, air, current, a "
        "signal): give a `flow` on every point where something moves, so the route draws itself across "
        "the picture step by step. Walk it in the order things happen. Never make a separate diagram for this: sketch_diagram "
        "is only for when they ask you to draw one. "
        "BILLS: a bill, invoice, insurance statement (EOB), phone or utility bill, or tax letter is explained ON "
        "it: style 'on_picture', `bill` FIRST (before `picture`), then `picture` (the page), then 4-7 tags, each "
        "with a `quote` of that line's exact words so it pins to the real line: what they owe, the due date, "
        "the charges that make up the total and why, and every line that looks wrong.",
        {
            "bill": {
                "type": "object",
                "description": "Bills only, given first: the verdict for the task tray.",
                "properties": {
                    "from": {"type": "string", "description": "Who sent it, e.g. 'Telus', 'CRA'."},
                    "owe": {
                        "type": "string",
                        "description": "Amount to pay, e.g. '$84.12' ('$0' if nothing).",
                    },
                    "due": {"type": "string", "description": "Pay by, e.g. '28 Nov'."},
                    "flags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What looks wrong, short: 'Roaming charged twice ($15)', "
                        "'Plan up $10 from last month', '$5 paper bill fee'. Empty if nothing.",
                    },
                },
            },
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
            "picture_window": {
                "type": "boolean",
                "description": "on_picture for a tour of the app they're in ('show me how to use "
                "CapCut', 'what does everything here do'): its whole window is the picture.",
            },
            "title": {"type": "string", "description": "2-4 words, e.g. 'Why this is funny'."},
            "closing": {
                "type": "string",
                "description": "Said after the last point: ONE short sentence, either the 'so that's why' "
                "or one specific offer of what to show next, e.g. 'Want me to sketch how the money burns?'",
            },
            "diagram": DIAGRAM_SCHEMA,
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
                            "description": "One short spoken sentence (under 15 words) for this point, "
                            "said as its mark draws.",
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
        "Teach them to do it themselves, in any app or site (Vercel, CapCut, Excel, Settings), one step at a "
        "time until they're done: mark the one thing to act on next, with an arrow to it, and say what to do "
        "and why in one short friendly sentence. Pyxi waits for them to do it (a click on the mark, or Done "
        "for other steps), looks at the new screen, and you give the next step (call guide again), until "
        "the goal is reached (done=true, with a cheerful line). Use `use_computer` instead when they want "
        "you to do it for them. If you're not sure where things are in this version of the app, web_search "
        "the current steps first. Before a step that inserts or pastes something at the cursor, first "
        "guide them to put the cursor where it belongs (e.g. click at the end of a line, press Enter). "
        "In a spreadsheet (Google Sheets, Excel) never point at or mention the small square or blue dot "
        "on a selection's corner: that's the fill handle, and dragging it copies one cell over all their "
        "data. To select a range, they click the first cell, then Shift+click the last one (or type the "
        "range, like A1:C13, in the Name box at the top left). Call done=true only when the screen shows "
        "the finished result (a pivot with its rows and values filled in, not just the empty editor). "
        "If the app isn't on screen any more (they switched away), don't guess: say one short line and "
        "stop calling guide, which ends the coaching.",
        {
            "action": {
                "type": "string",
                "enum": [
                    "click",
                    "double_click",
                    "right_click",
                    "type",
                    "press",
                    "scroll",
                    "drag",
                    "look",
                ],
                "description": "What they do at the mark (default click). 'type': what goes in the field "
                "is in `text`; 'press': keys in `text` (e.g. 'ctrl+s'); 'scroll': `direction`; 'drag': from the "
                "mark to `to_*`; 'look': just notice it.",
            },
            "element_id": {"type": "integer"},
            "quote": {
                "type": "string",
                "description": "Exact words on the control, from the OCR list.",
            },
            "box": {"type": "array", "items": {"type": "number"}},
            "label": {
                "type": "string",
                "description": "What it is, 1-4 words, e.g. 'Environment Variables'.",
            },
            "text": {
                "type": "string",
                "description": "For type: what to type (e.g. 'DATABASE_URL'); for press: the keys.",
            },
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "to_element_id": {"type": "integer"},
            "to_quote": {"type": "string"},
            "to_box": {"type": "array", "items": {"type": "number"}},
            "steps_total": {
                "type": "integer",
                "description": "Your best guess of how many steps the whole thing takes.",
            },
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
        "Hand a task to a teammate who works in the background. Browser agents (like Scout) drive a real browser, "
        "for work that needs a site worked step by step (a form, a sign-in, a cart, a booking, many pages compared "
        "in depth); that takes a minute or more, so never for 'find me / what's a good / the cheapest / the best X', "
        "which web_search answers in seconds. "
        "Writers (like Quill) draft text; explainers (like Sketch) build 3D scenes. Chain a handoff with then_agent/"
        "then_task, e.g. Scout researches then Quill drafts. Give complete, specific instructions. For files use "
        "convert_files (Pixel) and edit_document (Ledger) instead: they're faster and exact.",
        {
            "agent": {"type": "string"},
            "task": {"type": "string"},
            "start_url": {"type": "string"},
            "then_agent": {"type": "string"},
            "then_task": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths of documents on this PC for a writer to fix, translate or "
                "rewrite (.docx, .txt, .md, .pdf) or summarize (also .xlsx, .pptx, .html); the new "
                "copy is saved beside the original. Say what to do in task ('Summarize the key "
                "dates', 'Translate into French').",
            },
        },
        ["agent", "task"],
    ),
    _tool(
        "weather",
        "The forecast from a weather service, with real numbers: now, and each day for a week (high, "
        "low, sky, chance of rain). Use it for ANY weather question (today, tomorrow, the weekend, "
        "'should I bring an umbrella', 'is it cold out'), never web_search. Answer in one short "
        "sentence with the numbers that matter.",
        {
            "place": {
                "type": "string",
                "description": "The city they asked about; leave it empty for where they are.",
            }
        },
        [],
    ),
    _tool(
        "web_search",
        "Search the web (the answer is a few seconds away). Use it for anything that changes or that you're not sure of: "
        "news, prices, scores, results, release dates, who runs what, whether a claim or quote is "
        "real, facts about a specific person, product or place. It is also how you answer 'find me / what's a "
        "good / the cheapest / the best X' (a product, an app, a service): search, then say your pick with its "
        "price and source and put it on a card with show_answer, in the same turn, instead of sending a teammate "
        "to click through a shop. Searches the web and the news together, and "
        "news comes with its outlet and age. In the reply where you search, say one short starting line, never "
        "the answer: for a quick fact (the news, a score, a date, whether a claim is real) a few words "
        "('Checking the news.'); for a pick or a comparison ('find me', 'the best', 'the cheapest'), the "
        "criterion you're using or what you're comparing. Answer once the results are back, naming the sources. One search is "
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
            "role": {
                "type": "string",
                "enum": ["browser", "writer", "explainer", "files", "documents"],
            },
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
        "line first that names the job ('Writing to Sam about Friday.') and they're told when it's "
        "done. Write the task completely and exactly: every name, email address, date and time as the "
        "user said them. If something essential is missing (like when the meeting is), ask the user "
        "first instead.",
        {"task": {"type": "string"}},
        ["task"],
    ),
    _tool(
        "subscriptions",
        "Find every subscription and recurring charge in the user's Gmail receipts (service, amount, "
        "how often, last charged) and the monthly total, in the background: 'what am I paying "
        "for?', 'find subscriptions I forgot', 'how much do I spend on subscriptions?'. "
        "action=cancel with the service ('cancel Netflix', 'stop my Adobe plan') opens its cancel "
        "page after an approval card; Pyxi never cancels anything itself, the user presses the "
        "final button. Say one short line that names the job; the result comes as a notification.",
        {
            "action": {"type": "string", "enum": ["find", "cancel"]},
            "service": {
                "type": "string",
                "description": "For cancel: the service, as they said it.",
            },
        },
    ),
    _tool(
        "returns",
        "Check the user's order and shipping emails for return windows, in the background: 'what "
        "can I still return?', 'when do I have to return the headphones?', 'keep track of my "
        "returns'. Lists each order with its return deadline and sets reminders a few days before "
        "each one closes, then keeps checking new orders daily. Say one short line that names the job; the "
        "list comes as a notification.",
        {},
    ),
    _tool(
        "quick_action",
        "Do a small thing for the user right now: open_url (a website), open_app (notepad, calculator, chrome, "
        "word, excel, spotify, discord, settings, 'sound settings', 'microphone privacy'...), copy (exact text to "
        "the clipboard), or insert (type a drafted message into the text field they're in, e.g. 'write a reply "
        "to this'), media (the music or video that's playing, in any app: value playpause, next, "
        "previous; instant, no mouse), or volume (Windows' volume, instant: a number like 30, a change "
        "like -15, or lower, a bit lower, higher, low, mute). Only when they asked for it.",
        {
            "action": {
                "type": "string",
                "enum": ["open_url", "open_app", "copy", "insert", "media", "volume", "screen_playpause"],
            },
            "value": {"type": "string", "description": "The URL, app name, or text."},
        },
        ["action", "value"],
    ),
    _tool(
        "paste_table",
        "Paste a whole table into whatever is open in front of them, at their cursor or selected cell, in "
        "one step (under a second, no mouse): a grid in any spreadsheet, a real table in any document, in "
        "any app, any browser tab and whatever account they're signed in to (Excel or Word on the web, "
        "Google Sheets or Docs, Excel, Word, Notion, Airtable, an email). Use it to put data, a sample "
        "table, a list of rows or an empty table into the file they're looking at whenever Ledger can't "
        "reach that file: it's Excel or Word in a browser tab, another app or site, or edit_document said "
        "it can't open it. It only fills cells: a cell that starts with = is a formula in a spreadsheet, "
        "but a pivot, a chart or formatting needs edit_document. It lands where their cursor is, so if "
        "nothing is selected yet say 'click the cell where it should start' first. Say one short line.",
        {
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
                "description": "The table, row by row, the header row first: [[\"Region\", \"Sales\"], "
                "[\"West\", 1200]]. Empty strings make an empty table of that size.",
            },
        },
        ["rows"],
    ),
    _tool(
        "book",
        "Book something for them in a browser they can watch: a dentist, chiropractor, doctor or "
        "clinic appointment, a haircut, movie tickets, a restaurant table. Scout finds the slot, fills "
        "the form from their saved details, asks them before the final booking click, and never pays: "
        "it hands them the browser at the payment page. Use this for any 'book', 'reserve', 'get me "
        "tickets' or 'make an appointment' that happens on a website (not calendar invites: errand).",
        {
            "what": {
                "type": "string",
                "description": "e.g. 'dental cleaning', '2 tickets for Dune: Part Three'.",
            },
            "where": {
                "type": "string",
                "description": "The place or site if they said one (e.g. 'Maple Grove Dental', 'Cineplex Marine Gateway').",
            },
            "url": {
                "type": "string",
                "description": "The booking page if you know it (an https link).",
            },
            "when": {
                "type": "string",
                "description": "Their timing, in their words plus the date: 'Saturday 8 Nov, morning'.",
            },
            "people": {
                "type": "string",
                "description": "How many, if it matters (tickets, a table).",
            },
            "notes": {
                "type": "string",
                "description": "Anything else they asked for: practitioner, seats, new patient.",
            },
        },
        ["what"],
    ),
    _tool(
        "fill_form",
        "Fill in a web form for them from their saved details and a document they name: a job "
        "application from their CV, a government or school form, a rental application. Scout fills "
        "it in a browser they can watch, asks them before the final Submit, and leaves passwords, "
        "card numbers and ID numbers (SIN, passport, health card) for them to type. Use it for 'fill "
        "this in from my CV', 'apply to this job with my resume', never use_computer.",
        {
            "what": {
                "type": "string",
                "description": "e.g. 'job application for Junior Analyst at Northwind'.",
            },
            "url": {
                "type": "string",
                "description": "The form's address: from the address bar on screen, or what they gave.",
            },
            "documents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full paths of the CV or documents to fill it from (search_files first).",
            },
            "where": {"type": "string", "description": "The organisation, if known."},
            "notes": {
                "type": "string",
                "description": "Anything they asked for: 'say I can start in January', 'salary 60k'.",
            },
        },
        ["what", "url"],
    ),
    _tool(
        "check_in_flight",
        "Check them in for a flight when online check-in opens: schedules Scout for exactly 24 hours "
        "before departure (or now, if it's already open). Scout skips paid seats and upgrades, and "
        "the boarding pass link comes in a notification. Take the details from the airline's "
        "confirmation email (gmail_search) or from what they said.",
        {
            "airline": {"type": "string", "description": "e.g. 'Air Canada'."},
            "flight": {"type": "string", "description": "e.g. 'AC 123'."},
            "confirmation": {
                "type": "string",
                "description": "The booking reference, e.g. 'K7Q2ZP'.",
            },
            "last_name": {"type": "string", "description": "As on the booking, if not theirs."},
            "departure": {
                "type": "string",
                "description": "YYYY-MM-DDTHH:MM, the departure airport's local time.",
            },
            "from": {"type": "string", "description": "Departure airport, e.g. 'YVR'."},
            "url": {
                "type": "string",
                "description": "The airline's check-in page if you know it (from the email).",
            },
            "notes": {
                "type": "string",
                "description": "e.g. 'both passengers', 'window seat if free'.",
            },
        },
        ["confirmation", "departure"],
    ),
    _tool(
        "show_file",
        "Open a bill, statement or letter on their screen only when they ask you to EXPLAIN it ('explain "
        "my phone bill PDF', 'what is this tax letter about'), so you can tag it on the page with "
        "explain_on_screen. A question about what a file says ('what's the total on invoice 1182?', 'when "
        "is it due?') is read_file, not this. Only inside the folders they allowed; path from search_files.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _tool(
        "play_music",
        "Play a song, album or video right now, in the background: Pyxi's own player opens minimised "
        "(they keep working, nothing takes their screen), checks it's really playing, and sets the "
        "volume if they asked. About 4 seconds, no mouse: always use this for 'play X', never "
        "use_computer. Afterwards, pause, next and previous are quick_action media keys.",
        {
            "query": {
                "type": "string",
                "description": "What to play, e.g. 'Billie Jean Michael Jackson'.",
            },
            "app": {
                "type": "string",
                "enum": ["youtube_music", "youtube", "spotify"],
                "description": "spotify when they say Spotify; youtube when they want the video; "
                "else youtube_music (songs, with a queue so 'next' works).",
            },
            "volume": {
                "type": "string",
                "description": "Only if they asked: 30, -15, lower, a bit lower, higher, low.",
            },
        },
        ["query"],
    ),
    _tool(
        "look_again",
        "Look at the screen again after something you opened loads (a Google Maps search, a site, an "
        "app), so you answer from what's really there and can mark it: e.g. quick_action open_url "
        "'https://www.google.com/maps/search/coffee+open+now', then look_again, then explain_on_screen "
        "tagging the best-rated places with why. Fast: use it instead of a teammate for one quick look.",
        {
            "seconds": {
                "type": "number",
                "description": "How long to let it load first (default 2.5).",
            },
            "screen": {
                "type": "string",
                "description": "With more than one monitor: look at that screen instead ('left', "
                "'middle', 'right'), e.g. 'what's on my left screen?'. Use seconds 0 then.",
            },
        },
    ),
    _tool(
        "tidy_folder",
        "Organise a messy folder (their Desktop, Downloads, Documents, Pictures) into tidy subfolders. "
        "First call it with just `folder` to see what's there (the list comes back to you); then call it "
        "again with `groups`: 3-8 folders with plain names by what things are (Screenshots, Documents, "
        "Images, Videos, Installers, Archives, Shortcuts, or a project or topic when files clearly belong "
        "together), every loose file in one group, leaving out only files that already fit nowhere. Sort "
        "like a sharp assistant would: when several files share a topic, give it its own folder "
        "(Resumes, Bank statements, Immigration forms, Invoices) instead of one big Documents pile. They "
        "see one Allow card with the plan. Files are only moved, never deleted or overwritten, and "
        "`undo` puts the last tidy back exactly.",
        {
            "folder": {
                "type": "string",
                "description": "desktop, downloads, documents or pictures (default desktop).",
            },
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string", "description": "e.g. 'Screenshots'"},
                        "files": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["folder", "files"],
                },
            },
            "undo": {"type": "boolean", "description": "Put the last tidy back."},
        },
    ),
    _tool(
        "use_computer",
        "Do it yourself on their PC with the mouse and keyboard, in any app: play a song (in YouTube Music "
        "from the taskbar, or a new tab, then come back), search Maps, rename or move files in Explorer, "
        "change a setting in an app, click through a site. Never for files, PDFs, images, Excel, Word, "
        "PowerPoint, Google Sheets or Google Docs: convert_files and edit_document do those in the "
        "background, in seconds, without their mouse. They see an Allow card first, watch the cursor move, and can stop it by "
        "moving the mouse or pressing Esc; anything that sends, buys, posts or deletes asks again. Use it "
        "whenever they ask you to DO something on screen; use `guide` instead when they want to learn to "
        "do it themselves. Give `task` exactly: what to do, in which app, and what done looks like, plus "
        "what they told you (the song, the file). Say one short line first that names the job, never the task "
        "itself.",
        {
            "task": {
                "type": "string",
                "description": "e.g. 'Open YouTube Music from the taskbar, search Billie Jean by Michael "
                "Jackson, play the first song result, then switch back to the window they were in.'",
            },
            "app": {
                "type": "string",
                "description": "The app it happens in, if not the one they're in (e.g. 'YouTube Music').",
            },
            "come_back": {
                "type": "boolean",
                "description": "Put the window they're in back in front when done (music playing in "
                "the background, a quick lookup). False when they want to see the result (a sheet).",
            },
        },
        ["task"],
    ),
    _tool(
        "arrange_windows",
        "Arrange their windows, right now, in one call: two, three or four windows side by side, stacked or in "
        "a grid (layout 'panes'), split the screen in two ('split'), or snap, maximize, minimize or restore the "
        "window they're in. Any request to lay windows out goes here, never to use_computer: this places "
        "every window exactly and instantly. Just do it when asked; never tell them to do it themselves.",
        {
            "layout": {
                "type": "string",
                "enum": [
                    "panes",
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
            "panes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For panes: what goes in each part, in order (left to right, or top to "
                "bottom; for a grid: top left, top right, bottom left, bottom right). Each is 'current' "
                "(the window they're in), a website (https://stake.com, https://mail.google.com), an app "
                "(notepad, spotify), or a window that's already open (Gmail, CapCut). Open windows are "
                "reused; the rest open in new windows of their browser.",
            },
            "arrangement": {
                "type": "string",
                "enum": ["columns", "rows", "grid", "main"],
                "description": "For panes: columns side by side (default for 2 or 3), rows stacked, grid "
                "2x2 (default for 4), or main: a big left half with the rest stacked on the right.",
            },
            "screen": {
                "type": "string",
                "description": "With more than one monitor: which screen ('left', 'middle', 'right', "
                "'main'). With one pane (or none: the window they're in) it moves that window there "
                "full-screen ('put YouTube on my right screen'); with several it lays them out there.",
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
        "Set up something Pyxi does on a schedule while it's running, e.g. 'every weekday at 9 check my email for "
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
            "alarm": {
                "type": "boolean",
                "description": "An alarm: at that time Pyxi rings (a chime, a card, a spoken line) instead "
                "of doing a task. `task` is its label ('Gym', 'Alarm'). For 'every weekday at 7 wake me up'.",
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
        "convert_files",
        "Pixel does any file or PDF job in the background, in seconds, with no app window and without "
        "their mouse: Word, Excel, PowerPoint, Markdown, HTML or text to PDF; PDF to Word; images to "
        "PDF; merge, split, shrink, rotate, reorder, delete or extract pages; page numbers; a "
        "watermark; lock or unlock with a password (they type it on a card: never ask for it or put "
        "it in options); fill a PDF form from their saved details; text out of a PDF or photo (OCR "
        "for scans); a searchable PDF from a scan; compare two documents; HEIC to JPG; resize or "
        "shrink images; CSV and Excel. Needs real paths: from what's on screen (an Explorer window, "
        "an attachment you downloaded), from search_files, or from them. The original is never "
        "changed; the new file goes beside it. Say one short line that names the job; they get a notification.",
        {
            "operation": {
                "type": "string",
                "enum": list(PIXEL_OPERATIONS),
                "description": " ".join(f"{k}: {v}" for k, v in PIXEL_OPERATIONS.items()),
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full paths, in order (the order of pages when merging).",
            },
            "options": {
                "type": "object",
                "description": "Optional: pages ('1-2, 3' for split_pdf, extract_pages, "
                "delete_pages, rotate_pages, pdf_to_images; 'last' works), order ('3, 1-2' for "
                "reorder_pages), angle (90, 180, 270, left, right), text (the watermark), "
                "position ('bottom right', 'top center') and format ('Page {n} of {total}') for "
                "page numbers, values ({field name: value} for fill_form, only what they told "
                "you), name (new file's name, no extension), format (jpg, png, webp...), "
                "quality (1-95), max_side, width, height, percent, dpi, sheet. Never a password.",
            },
        },
        ["operation", "files"],
    ),
    _tool(
        "edit_document",
        "Ledger edits spreadsheets, documents and decks in the background, straight in the file, with no "
        "Office license and no app window. It does what a person would do by hand in Excel, Word and "
        "PowerPoint files and in Google Sheets, Docs and Slides: cells, formulas, totals, pivots, "
        "charts (add, move, resize, retitle, delete), sorting, filters, freezing rows, conditional "
        "colours, dropdowns, column widths, merging, tabs; headings, paragraphs, bullets, fonts and "
        "colours, spacing, tables (add one, fill or change its cells, add or delete rows and columns, "
        "shade it), pictures, page margins and orientation, headers and footers; slides, layouts, "
        "text boxes, shapes, pictures, tables and charts on a slide, moving and resizing things, "
        "backgrounds, speaker notes. Changing something already there (the table in this doc, the "
        "chart on this sheet) is this tool too: say which one in the task. A local file is saved as "
        "a copy beside the original; Google files are edited in place. For fixing the grammar of a "
        "document, delegate to Quill instead. Say one short line that names the job; they get a "
        "notification.",
        {
            "task": {
                "type": "string",
                "description": "Exactly what to change, with every value they gave, e.g. 'Add a "
                "totals row under the Amount column' or 'Make a pivot of total sales by region'.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full paths of .xlsx, .docx or .pptx files on this PC.",
            },
            "google": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Google Sheets, Docs or Slides: the link (from the address bar), "
                "words from the file's name ('sales sheet'), 'this sheet' / 'this doc' / 'this "
                "deck' for the one open on screen, or 'new sheet: <title>' / 'new doc: <title>' / "
                "'new slides: <title>' to make a new one.",
            },
        },
        ["task"],
    ),
    _tool(
        "search_files",
        "Find files on their PC by name or contents (only in the folders they allowed; the first time, "
        "they see a card asking if Pyxi may search their files). Use it to get the exact path for "
        "convert_files, edit_document or read_file when they name a file without saying where it is.",
        {
            "query": {
                "type": "string",
                "description": "Words from the file's name or contents, e.g. 'budget 2026'.",
            },
            "kind": {
                "type": "string",
                "enum": ["pdf", "image", "word", "excel", "powerpoint", "text"],
            },
        },
        ["query"],
    ),
    _tool(
        "read_file",
        "Read a file on their PC (text, PDF, Word, Excel, PowerPoint; an image's size) to answer a "
        "question about it. Only inside the folders they allowed.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _tool(
        "pin_note",
        "Pin a sticky note to an element; it reappears on that element whenever this app is open.",
        {"element_id": {"type": "integer"}, "note": {"type": "string"}},
        ["element_id", "note"],
    ),
]

INFO_TOOLS = {
    "look_again",
    "cancel_routine",  # back to the model: "change my alarm" is cancel, then set the new one
    "weather",
    "web_search",
    "read_page",
    "list_routines",
    "recall_memories",
    "recall_screen",
    "look_closer",
    "get_activity",
    "search_files",
    "read_file",
    "show_file",
}  # results come back to the model for another round

# "Is this cheaper anywhere?": Scout checks other stores and pins a tag (companion/pricecheck.py).
from pyxi.companion.pricecheck import TOOL as CHECK_PRICES  # noqa: E402

COMPANION_TOOLS.append(CHECK_PRICES)

# The answer to a "find me / what's a good / cheapest X", on a card (companion/answers.py).
from pyxi.companion.answers import TOOL as SHOW_ANSWER  # noqa: E402

COMPANION_TOOLS.append(SHOW_ANSWER)

# Specialist teammates: Atlas (travel), Penny (money), Scribe (meetings), Sage (study), Patch (code), Ditto (watch me once), Trace (memory) (companion/specialists.py).
from pyxi.companion.specialists import INFO as SPECIALIST_INFO  # noqa: E402
from pyxi.companion.specialists import SPECS as SPECIALIST_TOOLS  # noqa: E402

COMPANION_TOOLS += SPECIALIST_TOOLS
INFO_TOOLS |= SPECIALIST_INFO

# Pyxi in your browser: read_tab and use_tab (companion/tabwork.py). They are not in
# COMPANION_TOOLS: the brain adds them to a turn only while the extension is connected.
from pyxi.companion.tabwork import INFO as TAB_INFO  # noqa: E402

INFO_TOOLS |= TAB_INFO
