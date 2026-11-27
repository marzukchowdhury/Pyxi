"""The ears are told the names that are likely to be said.

Live, across the founder's trials: "GenOffice" was heard as "Jen Office", "TechLand" as
"Deckland", "SAFAR" as "Supper", and "marz at the rate of gmail.com" as "Mars at the rate of
Chimeel.com". The listening model on the PC takes a short list of words to expect; it had a
fixed one. It now gets the programs and pages that are open and what is on the desktop too.
"""

from __future__ import annotations

from pyxi.voice import stt


def test_the_standing_words_come_first_and_names_are_added_once_as_many_as_fit():
    words = stt.hotwords(["GenOffice", "Mozilla Firefox", "TechLand BD", "gmail", "x", "A, B", "GenOffice"])
    assert words.startswith(stt.HOTWORDS)
    assert words.endswith("GenOffice, Mozilla Firefox, TechLand BD")  # no repeat, no "x", no list in a name
    assert stt.hotwords(None) == stt.HOTWORDS and stt.hotwords([]) == stt.HOTWORDS
    # The name, without what a page adds to it.
    assert stt.hotwords(["SAFAR (Official Video)", "Lofi [4K]"]).endswith("SAFAR, Lofi")
    many = stt.hotwords([f"Program number {i}" for i in range(200)])
    assert len(many) <= stt.MAX_HINT_CHARS and many.startswith(stt.HOTWORDS)


def test_an_address_said_aloud_is_written_as_an_address():
    tidy = stt._tidy
    assert tidy("open Gmail in marz 23 at the rate of gmail.com") == "open Gmail in marz 23@gmail.com"
    assert tidy("send it to tarek at the rate gmail dot com please") == "send it to tarek@gmail.com please"
    # The same words that are not an address stay as they are.
    assert tidy("at the rate of ten per hour") == "at the rate of ten per hour"
    assert tidy("what is the rate of exchange dot com bubble") == "what is the rate of exchange dot com bubble"
    assert tidy("(no speech)") == ""


async def test_the_brain_gives_the_ears_what_is_open_and_on_the_desktop(settings, monkeypatch):
    from pyxi import windows
    from pyxi.companion import actions
    from test_brain_and_server import make_brain

    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(
        windows,
        "open_windows",
        lambda minimized=False: [
            (1, "Inbox (42) - me@example.com - Gmail - Mozilla Firefox", ""),
            (2, "Untitled Spreadsheet.xlsx - GenOffice", ""),
            (3, "TechLand BD | Trusted Computer Store", ""),
        ],
    )
    monkeypatch.setattr(actions, "_desktop_shortcuts", lambda: [("Bank statements", "x"), ("OBS Studio", "y")])
    brain, _ = make_brain(settings)
    names = brain._names_in_reach()
    assert {"Gmail", "Mozilla Firefox", "GenOffice", "TechLand BD", "Bank statements", "OBS Studio"} <= set(names)
    assert not any("@" in n or "42" in n for n in names)  # no addresses, no counts


# ---------------------------------------------------------------------- listening in the cloud
class _Cloud:
    """The model that listens, as far as the ears use it."""

    def __init__(self, text: str = "Find earphones on this website.", fail: bool = False) -> None:
        self.text, self.fail, self.heard = text, fail, []

    def available(self) -> bool:
        return True

    async def transcribe(self, samples, language, hints=None):
        self.heard.append((language, list(hints or [])))
        if self.fail:
            raise RuntimeError("HTTP 503: no connection")
        return self.text


def _ears(monkeypatch, cloud: _Cloud, **config):
    from pyxi.config import VoiceConfig

    t = stt.Transcriber(VoiceConfig(stt_model="base.en", stt_device="cpu", **config))
    t.ears = cloud
    local: list[str] = []

    async def on_this_pc(name, samples, language):
        local.append(name)
        return "Find airphones on this website.", language or "en"

    async def got(name):
        return object()

    monkeypatch.setattr(stt, "decode_audio", lambda audio: [0.0] * 16000)
    monkeypatch.setattr(t, "_local", on_this_pc)
    monkeypatch.setattr(t, "_get", got)
    return t, local


async def test_speech_is_written_down_by_the_cloud_model_with_the_names_in_reach(monkeypatch):
    """The founder's choice, after a day of "earphones" heard as "Airphones"."""
    cloud = _Cloud()
    t, local = _ears(monkeypatch, cloud)
    t.hints = ["GenOffice", "TechLand BD"]
    text, _, spoken = await t.transcribe(b"clip", "en")
    assert text == "Find earphones on this website." and spoken == "en"
    assert cloud.heard == [("en", ["GenOffice", "TechLand BD"])] and local == []
    # The words it is told to expect are for spelling only, and the standing ones are there too.
    said = stt.CloudEars.prompt("en", t.hints)
    assert "GenOffice" in said and "Pyxi" in said and "never add a word they did not say" in said
    assert "at the rate of gmail dot com" in said


async def test_offline_or_switched_off_this_pc_listens(monkeypatch):
    # The cloud can't be reached: the PC takes over, and they are still heard.
    cloud = _Cloud(fail=True)
    t, local = _ears(monkeypatch, cloud)
    text, _, _ = await t.transcribe(b"clip", "en")
    assert text == "Find airphones on this website." and local == ["base.en"] and len(cloud.heard) == 1
    # Their switch in the Voice panel: nothing of their voice is sent.
    cloud = _Cloud()
    t, local = _ears(monkeypatch, cloud)
    t.on_this_pc = True
    await t.transcribe(b"clip", "en")
    assert cloud.heard == [] and local == ["base.en"]
    # Longer than the cloud takes in one piece: the PC, so nothing they said is cut off.
    cloud = _Cloud()
    t, local = _ears(monkeypatch, cloud)
    monkeypatch.setattr(stt, "decode_audio", lambda audio: [0.0] * (stt.MAX_CLOUD_SECONDS * 16000 + 1))
    await t.transcribe(b"clip", "en")
    assert cloud.heard == [] and local == ["base.en"]
    monkeypatch.setattr(stt, "decode_audio", lambda audio: [0.0] * 16000)
    # And the setting, for a PC that must stay offline.
    cloud = _Cloud()
    t, local = _ears(monkeypatch, cloud, stt_cloud=False)
    await t.transcribe(b"clip", "en")
    assert cloud.heard == [] and local == ["base.en"]


def test_the_switch_is_one_of_the_voice_settings():
    from pyxi.server import VOICE_KEYS
    from pyxi.voice.studio import VoicePrefs

    assert VoicePrefs().listen_here is False and VOICE_KEYS["listen_here"] == "listen_here"
    from pathlib import Path

    panel = (Path(__file__).parents[1] / "desktop/src/renderer/src/panel/VoiceStudio.tsx").read_text(encoding="utf-8")
    assert "Turn my speech into text on this PC only" in panel and 'set("listen_here"' in panel
