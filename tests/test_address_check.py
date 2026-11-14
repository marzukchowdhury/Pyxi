"""'Is this the address you meant?': the card before Pyxi acts on an email address the user gave.
When it appears, when it does not, what it turns spoken addresses into, and what reaches the
tool call afterwards."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from pyxi import booking
from pyxi.companion import addresscheck as ac
from test_brain_and_server import make_brain


@pytest.fixture
def brain(settings):
    b, rec = make_brain(settings)
    b.rec = rec
    b._ctx = None
    b._turn_reads = []
    b._synthetic = False
    b._turn_id = "turn_test"
    b.errands = []
    b.searches = []

    async def run_errand(task, words, **kw):
        b.errands.append((task, words))

    async def search(args):
        b.searches.append(dict(args))
        return "Emails (newest first):"

    b._run_errand = run_errand
    tool = SimpleNamespace(handler=search, spec=SimpleNamespace(name="gmail_search"))
    b.connectors.find = lambda name: tool if name.startswith("gmail_") else None
    b.connectors.tools = lambda: [tool]
    return b


def _answer(b, value):
    """Every card is answered with ``value`` ("yes" keeps what the card shows)."""

    async def request(kind, timeout=300, **data):
        b.rec.events.append({"type": kind, "timeout": timeout, **data})
        if value == "yes":
            return {"addresses": [a["address"] for a in data["addresses"]]}
        return value

    b.hub.request = request  # type: ignore[method-assign]


def _cards(b):
    return [e for e in b.rec.events if e["type"] == "check_address"]


async def _errand(b, said, task=None):
    b._user_text = said
    note = await b._act("errand", {"task": task or said})
    await asyncio.sleep(0)
    return note


# ------------------------------------------------------------------ normalising
@pytest.mark.parametrize(
    "words, address",
    [
        ("email sam at example dot com about lunch", "sam@example.com"),
        ("send it to sam@example. com please", "sam@example.com"),
        ("write to sam @ example.com.", "sam@example.com"),
        ("it's s a m underscore 21 at example dot com", "sam_21@example.com"),
        ("mail dana dot lee at the rate of example dot com", "dana.lee@example.com"),
        ("ask jo dash ann at mail dot example dot org, today", "jo-ann@mail.example.org"),
        ("Sam Lee at Example dot co dot uk", "lee@example.co.uk"),
        ("to Dana.Lee@Example.com!", "Dana.Lee@example.com"),
        ("find mail from sam@example", "sam@example"),
    ],
)
def test_spoken_and_spaced_addresses_become_real_ones(words, address):
    assert [f.address for f in ac.find(words)] == [address]


def test_plain_words_hold_no_address():
    assert ac.find("look at this and tell me what it says. Dot the i's.") == []
    assert ac.find("meet me at noon") == []


def test_what_looks_wrong_is_said_in_a_few_words():
    assert ac.problem("sam@example.com") is None
    assert ac.problem("sam@example") == "The part after the @ needs a dot, like example.com."
    assert ac.problem("sam.example.com") == "There is no @ in it."
    assert ac.problem("sam@@example.com") == "There is more than one @."
    assert ac.problem("sam @example.com") == "There is a space in it."
    assert ac.problem("@example.com") == "Nothing comes before the @."
    assert ac.problem("sam@example..com") == "There are two dots in a row."
    assert ac.problem("sam@example.c0m") == "The ending after the last dot looks wrong."


# ------------------------------------------------------------------ near misses
def test_a_known_domain_misspelt_offers_the_right_one():
    for typo, right in [
        ("gmial.com", "gmail.com"),
        ("gmal.com", "gmail.com"),
        ("hotmial.com", "hotmail.com"),
        ("yaho.com", "yahoo.com"),
        ("outlok.com", "outlook.com"),
        ("example.con", "example.com"),
    ]:
        assert ac.suggest(f"sam@{typo}", set()) == f"sam@{right}"


def test_a_slip_from_a_known_address_offers_it_and_a_stranger_offers_nothing():
    known = {"dana.lee@example.com", "me@example.org"}
    assert ac.suggest("dana.le@example.com", known) == "dana.lee@example.com"
    assert ac.suggest("dana.lee@exmaple.com", known) == "dana.lee@example.com"  # two swapped
    assert ac.suggest("robin@example.net", known) is None
    # Two known addresses equally close: no guess.
    assert ac.suggest("sam@example.com", {"pam@example.com", "tam@example.com"}) is None


# ------------------------------------------------------------------ when it appears
async def test_an_address_they_said_is_checked_before_the_errand_starts(brain):
    _answer(brain, "yes")
    assert await _errand(brain, "email sam@example.com that I'm running late") is None
    [card] = _cards(brain)
    assert card["addresses"] == [
        {"address": "sam@example.com", "suggestion": None, "problem": None, "heard": None}
    ]
    assert card["timeout"] == 300 and card["voice"] is False
    assert brain.errands == [("email sam@example.com that I'm running late",) * 2]


async def test_searching_mail_from_an_address_they_said_is_checked(brain):
    _answer(brain, "yes")
    brain._user_text = "find the mail from robin@example.net"
    await brain._act("gmail_search", {"from": "robin@example.net"})
    assert len(_cards(brain)) == 1 and brain.searches == [{"from": "robin@example.net"}]


async def test_an_address_typed_into_a_form_or_handed_to_a_teammate_is_checked(brain):
    _answer(brain, False)
    brain._user_text = "sign me up on that page with robin@example.net"
    for name, args in [
        ("fill_form", {"what": "newsletter", "notes": "email robin@example.net"}),
        ("delegate", {"agent": "Scout", "task": "Sign up with robin@example.net"}),
        ("use_computer", {"task": "Type robin@example.net into the Email box"}),
    ]:
        brain._address_turn = None
        assert (await brain._act(name, args)).startswith(f"{name}: they said never mind")
    assert len(_cards(brain)) == 3


async def test_one_card_a_turn_for_several_calls_and_several_addresses(brain):
    _answer(brain, "yes")
    brain._user_text = "forward what sam@example.com sent to robin@example.net"
    await brain._act("gmail_search", {"from": "sam@example.com"})
    await brain._act("errand", {"task": "Forward Sam's mail to robin@example.net"})
    await brain._act("gmail_search", {"to": "robin@example.net"})
    [card] = _cards(brain)
    assert [a["address"] for a in card["addresses"]] == ["sam@example.com", "robin@example.net"]


async def test_heard_by_voice_it_says_one_line_and_shows_the_raw_words(brain):
    _answer(brain, "yes")
    said = "email s a m underscore 21 at example dot com that I'm late"
    brain._heard_text = said
    await _errand(brain, said, task="Email sam_21@example.com: running late")
    [card] = _cards(brain)
    assert card["voice"] is True
    assert card["addresses"][0]["address"] == "sam_21@example.com"
    assert card["addresses"][0]["heard"] == "s a m underscore 21 at example dot com"
    await asyncio.gather(brain._speech)
    assert [e["text"] for e in brain.rec.of("say")] == ["Just checking the address."]
    # The errand is handed their words with the address as it was confirmed.
    assert brain.errands[0][1] == "email sam_21@example.com that I'm late"


async def test_an_address_said_a_turn_ago_is_still_theirs(brain):
    _answer(brain, "yes")
    brain.history = [("email robin@example.net", "What should it say?")]
    await _errand(brain, "that I'm running late", task="Email robin@example.net: running late")
    assert len(_cards(brain)) == 1


async def test_it_cannot_be_an_address_so_the_card_says_what_is_wrong(brain):
    _answer(brain, {"addresses": ["sam@example.com"]})
    await _errand(brain, "email sam@example about lunch", task="Email sam@example about lunch")
    [card] = _cards(brain)
    assert card["addresses"][0]["problem"] == "The part after the @ needs a dot, like example.com."
    assert brain.errands[0][0] == "Email sam@example.com about lunch"


async def test_a_near_miss_is_shown_with_the_likely_address(brain):
    _answer(brain, "yes")
    ac.Book(brain.settings.data_dir).add("dana.lee@example.com")
    await _errand(brain, "email dana.le@example.com and sam@gmial.com")
    [card] = _cards(brain)
    assert [a["suggestion"] for a in card["addresses"]] == ["dana.lee@example.com", "sam@gmail.com"]


# ------------------------------------------------------------------ when it does not
async def test_their_own_addresses_are_never_asked_about(brain):
    _answer(brain, False)  # any card would stop the errand
    booking.save(brain.settings.data_dir, {"first_name": "Dana", "email": "dana@example.com"})
    brain.connectors.google.email = "Dana.Work@example.org"
    await _errand(brain, "email dana@example.com and dana.work@example.org my notes")
    assert not _cards(brain) and len(brain.errands) == 1


async def test_confirmed_once_it_is_remembered_and_not_asked_again(brain):
    _answer(brain, "yes")
    await _errand(brain, "email robin@example.net hello")
    brain._turn_id = "turn_next"
    await _errand(brain, "email Robin@Example.net again")
    assert len(_cards(brain)) == 1 and len(brain.errands) == 2
    saved = json.loads((brain.settings.data_dir / ac.FILE).read_text(encoding="utf-8"))
    assert saved == ["robin@example.net"]


async def test_an_address_pyxi_found_itself_is_not_asked_about(brain):
    _answer(brain, False)
    brain._user_text = "reply to the landlord's last email"
    brain._turn_reads = [("the email you read", "From: Lee Park <lee.park@example.org>")]
    await brain._act("gmail_search", {"from": "lee.park@example.org"})
    await brain._act("errand", {"task": "Reply to lee.park@example.org: yes, Friday works"})
    await asyncio.sleep(0)
    assert not _cards(brain) and len(brain.errands) == 1 and len(brain.searches) == 1


async def test_someone_already_in_their_mail_is_not_asked_about(brain):
    _answer(brain, False)
    brain.connectors.google._names = {"robin@example.net": "Robin Shaw"}
    await _errand(brain, "email robin@example.net that I'm late")
    assert not _cards(brain) and len(brain.errands) == 1


async def test_a_routine_or_a_tool_that_acts_on_nothing_never_asks(brain):
    _answer(brain, False)
    brain._user_text = "what is robin@example.net's company?"
    assert await ac.before(brain, "web_search", {"query": "robin@example.net"}) is None
    brain._synthetic = True
    assert await ac.before(brain, "errand", {"task": "Email robin@example.net the report"}) is None
    assert not _cards(brain)


# ------------------------------------------------------------------ what happens next
async def test_the_corrected_address_reaches_every_tool_call(brain):
    _answer(brain, {"addresses": ["robin@example.net"]})
    brain._user_text = "find mail from robbin@example.net and email robbin@example.net thanks"
    await brain._act("gmail_search", {"from": "Robbin@example.net"})
    await brain._act("errand", {"task": "Email robbin@example.net: thanks"})
    await asyncio.sleep(0)
    assert brain.searches == [{"from": "robin@example.net"}]
    assert brain.errands == [
        (
            "Email robin@example.net: thanks",
            "find mail from robin@example.net and email robin@example.net thanks",
        )
    ]
    assert len(_cards(brain)) == 1
    # The corrected one is remembered, not the slip.
    assert ac.Book(brain.settings.data_dir).all() == {"robin@example.net"}


async def test_a_correction_the_task_never_named_is_added_to_the_errand(brain):
    _answer(brain, {"addresses": ["robin@example.net"]})
    await _errand(brain, "email robbin@example.net thanks", task="Email Robin: thanks")
    assert brain.errands[0][0] == "Email Robin: thanks (The address they confirmed: robin@example.net.)"


@pytest.mark.parametrize(
    "value, line, note",
    [
        (False, "Okay, I've left it, and nothing was sent.", "they said never mind"),
        (None, "The address wasn't confirmed, so I've left it.", "was not confirmed"),
        ({"addresses": ["not an address"]}, "The address wasn't confirmed, so I've left it.", "was not confirmed"),
    ],
)
async def test_cancel_or_no_answer_sends_and_searches_nothing(brain, value, line, note):
    _answer(brain, value)
    brain._user_text = "find mail from robin@example.net and email robin@example.net thanks"
    first = await brain._act("gmail_search", {"from": "robin@example.net"})
    second = await brain._act("errand", {"task": "Email robin@example.net: thanks"})
    assert note in first and first.startswith("gmail_search:") and second.startswith("errand:")
    await asyncio.sleep(0)
    assert brain.errands == [] and brain.searches == [] and len(_cards(brain)) == 1
    await asyncio.gather(brain._speech)
    assert [e["text"] for e in brain.rec.of("say")] == [line]
    assert not (brain.settings.data_dir / ac.FILE).exists()


async def test_a_card_that_breaks_cancels_the_action(brain):
    async def request(kind, **data):
        raise RuntimeError("no window")

    brain.hub.request = request  # type: ignore[method-assign]
    note = await _errand(brain, "email robin@example.net hello")
    assert "was not confirmed" in note and brain.errands == [] and not ac.waiting(brain)


async def test_the_send_approval_card_still_comes_after_the_check(brain, monkeypatch):
    """The address check is first; it does not stand in for the approval before a send."""
    from pyxi.connectors import Outgoing

    async def run():
        return "Sent."

    async def send(args):
        return Outgoing(
            action=f"Send email to {args['to']}", preview="Hello", run=run, recipients=[args["to"]]
        )

    brain.connectors.find = lambda name: SimpleNamespace(handler=send)
    approved = []

    async def approve(out):
        approved.append(out.action)

    monkeypatch.setattr(brain, "_approve", approve)
    _answer(brain, {"addresses": ["robin@example.net"]})
    brain._user_text = "send robbin@example.net a hello"
    note = await brain._act("gmail_send", {"to": "robbin@example.net", "body": "Hello"})
    await asyncio.sleep(0)
    assert len(_cards(brain)) == 1
    assert approved == ["Send email to robin@example.net"] and "Waiting for the user" in note


# ------------------------------------------------------------------ answering aloud
async def _waiting_card(brain, said):
    """The errand call up to its card, and the future that answers the card."""
    asked: asyncio.Future = asyncio.get_running_loop().create_future()
    answer: asyncio.Future = asyncio.get_running_loop().create_future()

    async def request(kind, timeout=300, **data):
        asked.set_result(data)
        return await answer

    brain.hub.request = request  # type: ignore[method-assign]
    brain._user_text = said
    call = asyncio.ensure_future(brain._act("errand", {"task": said}))
    await asked
    return call, answer


async def test_yes_aloud_confirms_and_a_spoken_correction_only_fills_the_box(brain):
    call, answer = await _waiting_card(brain, "email robbin@example.net hello")
    assert ac.waiting(brain)
    brain._speech_gen += 1  # they pressed talk: the waiting turn would count as talked over
    await brain.turn("it's robin at example dot net")
    await brain.turn("Yes, that's right.")
    said = brain.rec.of("address_voice")
    assert [e["action"] for e in said] == ["fill", "confirm"]
    assert said[0]["address"] == "robin@example.net"
    assert not call.done()  # the correction waited for the yes, and the yes for the card
    assert brain._turn_gen == brain._speech_gen  # the turn that asked carries on
    answer.set_result({"addresses": ["robin@example.net"]})
    assert await call is None
    await asyncio.sleep(0)
    assert brain.errands[0][0] == "email robin@example.net hello" and not ac.waiting(brain)


async def test_never_mind_and_no_aloud_and_other_words_pass_by(brain):
    call, answer = await _waiting_card(brain, "email robbin@example.net hello")
    assert await ac.heard(brain, "no") and await ac.heard(brain, "Never mind.")
    assert [e["action"] for e in brain.rec.of("address_voice")] == ["edit", "cancel"]
    assert not await ac.heard(brain, "what's the weather like?")
    answer.set_result(False)
    assert (await call).startswith("errand: they said never mind")
    assert not await ac.heard(brain, "yes")  # no card: "yes" is an ordinary word again


# ------------------------------------------------------------------ what is kept
async def test_wipe_everything_clears_the_confirmed_addresses(brain):
    book = ac.Book(brain.settings.data_dir)
    book.add("robin@example.net", "Sam@Example.com", "not an address")
    assert book.all() == {"robin@example.net", "sam@example.com"}

    async def request(kind, **data):
        return True

    brain.hub.request = request  # type: ignore[method-assign]
    await brain.wipe_everything()
    assert book.all() == set() and not book.path.exists()


def test_the_file_is_small_and_survives_being_damaged(tmp_path):
    book = ac.Book(tmp_path)
    book.path.write_text("{not json", encoding="utf-8")
    assert book.all() == set()
    book.add(*(f"person{i}@example.com" for i in range(ac.KEEP + 20)))
    assert len(book.all()) == ac.KEEP and "person0@example.com" not in book.all()
