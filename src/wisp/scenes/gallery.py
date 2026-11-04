"""Hand-authored scenes: an offline gallery that shows off the 3D layer with no API key,
and few-shot examples of what a great scene looks like."""

from __future__ import annotations

from wisp.scenes.spec import SceneEdge, SceneNode, SceneSpec, SceneStep


def _e(a: str, b: str, label: str | None = None, kind: str = "flow", **kw) -> SceneEdge:
    return SceneEdge(source=a, target=b, label=label, kind=kind, **kw)


def https() -> SceneSpec:
    return SceneSpec(
        title="How HTTPS keeps you safe",
        subtitle="What happens in the half-second before a padlock appears",
        layout="flow",
        theme="aurora",
        nodes=[
            SceneNode(id="you", label="Your browser", shape="user", detail="wants example.com"),
            SceneNode(id="dns", label="DNS resolver", shape="database", detail="name to address"),
            SceneNode(
                id="server", label="example.com", shape="server", detail="holds a certificate"
            ),
            SceneNode(
                id="ca",
                label="Certificate Authority",
                shape="cloud",
                detail="vouches for the server",
            ),
            SceneNode(
                id="key", label="Session key", shape="chip", glow=True, detail="shared secret"
            ),
        ],
        edges=[
            _e("you", "dns", "1. where is it?"),
            _e("you", "server", "2. hello + ciphers", kind="arrow"),
            _e("server", "you", "3. certificate"),
            _e("ca", "server", "signed it", kind="dashed"),
            _e("you", "key", "4. agree a key", kind="arrow"),
            _e("key", "server", "encrypted traffic", bidirectional=True),
        ],
        steps=[
            SceneStep(
                title="Finding the server",
                narration="First your browser asks DNS where example dot com actually lives.",
                focus=["you", "dns"],
                edges=["you->dns"],
            ),
            SceneStep(
                title="Saying hello",
                narration="Then it says hello to the server and lists the encryption it speaks.",
                focus=["you", "server"],
                edges=["you->server"],
            ),
            SceneStep(
                title="Proving identity",
                narration="The server answers with a certificate, signed by an authority your browser already trusts.",
                focus=["server", "ca"],
                edges=["server->you", "ca->server"],
            ),
            SceneStep(
                title="A shared secret",
                narration="Both sides agree on a fresh session key that nobody watching can work out.",
                focus=["key"],
                edges=["you->key"],
            ),
            SceneStep(
                title="Private from here on",
                narration="From then on, everything you send is encrypted with that key. That's your padlock.",
                focus=["key", "server", "you"],
                edges=["key->server"],
                camera="overview",
            ),
        ],
    )


def wisp_itself() -> SceneSpec:
    return SceneSpec(
        title="How Wisp works",
        subtitle="Ears, eyes, brain, hands and a voice",
        layout="orbit",
        theme="candy",
        nodes=[
            SceneNode(
                id="brain",
                label="Brain",
                shape="sphere",
                size=1.6,
                glow=True,
                detail="LangGraph + Claude or local model",
            ),
            SceneNode(id="ears", label="Ears", shape="capsule", detail="faster-whisper"),
            SceneNode(id="eyes", label="Eyes", shape="screen", detail="screenshot + UI Automation"),
            SceneNode(id="voice", label="Voice", shape="capsule", detail="Kokoro or Chatterbox"),
            SceneNode(
                id="hands", label="Agents", shape="server", detail="Otto drives a real browser"
            ),
            SceneNode(
                id="memory", label="Spatial memory", shape="database", detail="what was where"
            ),
            SceneNode(
                id="overlay", label="3D overlay", shape="torus", detail="points, draws, explains"
            ),
        ],
        edges=[
            _e("ears", "brain", "what you said"),
            _e("eyes", "brain", "what you see"),
            _e("memory", "brain", "what was there", kind="dashed"),
            _e("brain", "voice", "what to say"),
            _e("brain", "overlay", "what to show"),
            _e("brain", "hands", "jobs", kind="arrow"),
            _e("hands", "brain", "results"),
        ],
        steps=[
            SceneStep(
                title="Listening",
                narration="When you hold the hotkey, my ears turn your voice into text, right here on your machine.",
                focus=["ears", "brain"],
                edges=["ears->brain"],
            ),
            SceneStep(
                title="Looking",
                narration="At the same moment I take a look: a screenshot, plus the accessibility tree that says what every button is.",
                focus=["eyes", "brain"],
                edges=["eyes->brain"],
            ),
            SceneStep(
                title="Remembering",
                narration="I also remember where things were, so I can find that button you saw ten minutes ago.",
                focus=["memory"],
                edges=["memory->brain"],
            ),
            SceneStep(
                title="Showing",
                narration="Then I answer with my voice, and point, circle or build a scene like this one.",
                focus=["voice", "overlay"],
                edges=["brain->voice", "brain->overlay"],
            ),
            SceneStep(
                title="Doing",
                narration="And for real work, I send a teammate like Scout to drive a browser, while you keep watching.",
                focus=["hands"],
                edges=["brain->hands", "hands->brain"],
                camera="overview",
            ),
        ],
    )


def neural_net() -> SceneSpec:
    layers = {
        "in": ["x1", "x2", "x3"],
        "h1": ["a1", "a2", "a3", "a4"],
        "h2": ["b1", "b2", "b3"],
        "out": ["y"],
    }
    nodes = []
    for depth, (layer, ids) in enumerate(layers.items()):
        for nid in ids:
            nodes.append(
                SceneNode(id=nid, label=nid, shape="sphere", size=0.6, group=layer, depth=depth)
            )
    edges = [
        _e(a, b, kind="link")
        for la, lb in (("in", "h1"), ("h1", "h2"), ("h2", "out"))
        for a in layers[la]
        for b in layers[lb]
    ]
    return SceneSpec(
        title="How a neural network learns",
        subtitle="Forward pass, error, and a nudge backwards",
        layout="grid",
        theme="blueprint",
        nodes=nodes,
        edges=edges,
        steps=[
            SceneStep(
                title="Inputs",
                narration="Numbers come in on the left: pixels, words, whatever we're learning from.",
                focus=layers["in"],
            ),
            SceneStep(
                title="Forward pass",
                narration="Each layer mixes what it receives with its weights and passes the result on.",
                focus=layers["h1"] + layers["h2"],
                edges=[f"{a}->{b}" for a in layers["in"] for b in layers["h1"]],
            ),
            SceneStep(
                title="A guess",
                narration="At the end, the network makes a guess and we measure how wrong it was.",
                focus=["y"],
            ),
            SceneStep(
                title="Learning",
                narration="Then the error flows backwards, nudging every weight a little so the next guess is better.",
                focus=[n.id for n in nodes],
                camera="overview",
            ),
        ],
    )


def git_branches() -> SceneSpec:
    return SceneSpec(
        title="Git branches, merged",
        subtitle="Why your feature branch doesn't break main",
        layout="timeline",
        theme="aurora",
        nodes=[
            SceneNode(id="c1", label="init", shape="sphere", size=0.7, group="main"),
            SceneNode(id="c2", label="add login", shape="sphere", size=0.7, group="main"),
            SceneNode(
                id="f1",
                label="try dark mode",
                shape="sphere",
                size=0.7,
                group="feature",
                color="#ec4899",
            ),
            SceneNode(
                id="f2",
                label="polish colours",
                shape="sphere",
                size=0.7,
                group="feature",
                color="#ec4899",
            ),
            SceneNode(id="c3", label="fix typo", shape="sphere", size=0.7, group="main"),
            SceneNode(id="m", label="merge", shape="sphere", size=0.9, group="main", glow=True),
        ],
        edges=[
            _e("c1", "c2", kind="link"),
            _e("c2", "c3", kind="link"),
            _e("c3", "m", kind="link"),
            _e("c2", "f1", "branch", kind="arrow"),
            _e("f1", "f2", kind="link"),
            _e("f2", "m", "merge", kind="flow"),
        ],
        steps=[
            SceneStep(
                title="Main moves on",
                narration="Main is a line of snapshots. Each commit points back to the one before.",
                focus=["c1", "c2", "c3"],
            ),
            SceneStep(
                title="A safe sandbox",
                narration="A branch starts from one commit, so you can experiment without touching main.",
                focus=["f1", "f2"],
                edges=["c2->f1"],
            ),
            SceneStep(
                title="Coming back together",
                narration="Merging weaves both histories into one new commit with two parents.",
                focus=["m"],
                edges=["f2->m"],
                camera="overview",
            ),
        ],
    )


GALLERY = {"https": https, "wisp": wisp_itself, "neural_net": neural_net, "git": git_branches}
TITLES = {name: fn().title for name, fn in GALLERY.items()}


def get_scene(name: str) -> SceneSpec:
    if name not in GALLERY:
        raise KeyError(f"No gallery scene {name!r}. Options: {', '.join(GALLERY)}")
    return GALLERY[name]()
