"""Browser work that cannot go on says why and what to do: never a note written for the
model read out loud, never a script error from the page, never silence where a result was
promised."""

from __future__ import annotations

from types import SimpleNamespace

from conftest import Recorder
from pyxi.agents.jobs import Job
from pyxi.browserlink import BrowserError
from pyxi.companion import paperwork, pricecheck, tabwork
from pyxi.hub import Hub


# ---------------------------------------------------------------- a page that would not open
def test_a_page_that_would_not_open_is_said_for_a_person():
    """The step's note is for the model. Live it was the whole spoken answer: "failed: the
    browser didn't answer, so it isn't known whether that happened. Read the page before
    doing anything else." """
    said = tabwork._not_opened(
        "failed: the browser didn't answer, so it isn't known whether that happened. "
        "Read the page before doing anything else."
    )
    assert said.startswith("Your browser didn't answer") and "then ask me again" in said
    assert "Read the page" not in said and not said.startswith("failed")

    said = tabwork._not_opened("failed: The browser extension isn't connected.")
    assert said == tabwork.NOT_CONNECTED and "Settings under Connect your browser" in said

    said = tabwork._not_opened("failed: the browser hasn't let Pyxi into shop.example.com yet")
    assert "Click the Pyxi icon" in said and "press Allow" in said and "shop.example.com" in said

    said = tabwork._not_opened("failed: that isn't a plain web address (http or https, nothing odd in it)")
    assert said.startswith("That doesn't look like a web address") and "http" not in said

    # A script error from the page, passed on by the extension, is never read out.
    said = tabwork._not_opened("failed: TypeError: Cannot read properties of undefined (reading 'id')")
    assert said == "I couldn't open that page. Open it yourself, then ask me again."

    # A plain line of the extension's own is kept.
    said = tabwork._not_opened("failed: That address leads to another site, so I didn't open it.")
    assert said == "I couldn't open that page. That address leads to another site, so I didn't open it."


def test_what_the_extension_said_is_only_passed_on_when_it_is_a_sentence():
    assert tabwork._for_them(BrowserError("gone", "That tab was closed.")) == "That tab was closed."
    assert tabwork._for_them(BrowserError("failed", "TypeError: x is not a function")) == ""
    assert tabwork._for_them(BrowserError("failed", "Error: Tabs cannot be edited right now (user may be dragging a tab).")) == ""


# ---------------------------------------------------------------- a site that shut Scout out
async def test_a_site_that_shut_scout_out_points_to_their_own_tab(settings, monkeypatch):
    from pyxi import plainwords as pw
    from test_audit_tabwork import _scout

    for summary in (
        "I couldn't get past the site's bot check, so I couldn't read the prices.",
        "The page asked me to sign in before showing the order history.",
        "The site wants a CAPTCHA solved, which I won't do.",
    ):
        job = await _scout(settings, monkeypatch, summary, success=False)
        assert job.status == "failed" and job.summary == summary + pw.OWN_TAB
        assert "your own browser" in job.summary and "ask me again" in job.summary
    # Other failures are left as they are, and so is a line that already says what to do.
    job = await _scout(settings, monkeypatch, "I couldn't open the shop's site: it kept timing out.", success=False)
    assert job.summary == "I couldn't open the shop's site: it kept timing out."
    assert pw.own_tab_hint(pw.MODEL_KEY_BETA) == "" and pw.own_tab_hint(pw.GOOGLE_EXPIRED) == ""
    # A found answer that mentions signing in is an answer, not a failure.
    job = await _scout(settings, monkeypatch, "The price is $20 once you sign in to the members' store.")
    assert job.status == "done" and pw.OWN_TAB not in job.summary


# ---------------------------------------------------------------- the price check
class FakeJobs:
    def __init__(self) -> None:
        self.settled: list[tuple[bool, str]] = []

    async def track(self, agent_name, task, role="errand"):
        return SimpleNamespace(id="job_1")

    async def settle(self, job, *, ok, summary, data=None, cost=0.0):
        self.settled.append((ok, summary))


def price_brain(settings):
    hub = Hub()
    said: list[str] = []

    async def say_when_free(line: str) -> None:
        said.append(line)

    spawned: list = []
    brain = SimpleNamespace(
        hub=hub, jobs=FakeJobs(), settings=settings, history=[], _ctx=None,
        screen=SimpleNamespace(capture=None), _spawn=spawned.append, _say_when_free=say_when_free,
    )  # fmt: skip
    return brain, Recorder(hub), spawned, said


async def test_a_price_check_that_breaks_says_so_instead_of_going_quiet(settings):
    """They were told "the tag by the price gives the answer", and no tag ever came."""
    brain, rec, spawned, said = price_brain(settings)

    async def offline(url):
        raise OSError(11001, "getaddrinfo failed")

    reader = SimpleNamespace(fetch=offline)
    check = await pricecheck.run_check(
        brain, {"name": "Wren Two earbuds", "price": "$25"}, "https://www.amazon.com/dp/B0EXAMPLE", reader=reader
    )
    assert check is None
    [(ok, summary)] = brain.jobs.settled
    assert not ok and summary.startswith("I couldn't check other stores just now.")
    assert "internet connection" in summary and "getaddrinfo" not in summary and "Errno" not in summary
    for task in spawned:
        await task
    assert said == [summary]  # said aloud, since they asked
    assert not rec.of("price_tag")


async def test_a_quiet_check_of_its_own_stays_quiet(settings):
    brain, _, spawned, said = price_brain(settings)

    async def broken(url):
        raise KeyError("offers")

    await pricecheck.run_check(
        brain, {"name": "Wren Two", "price": "$25"}, "https://www.amazon.com/dp/B0EXAMPLE",
        auto=True, reader=SimpleNamespace(fetch=broken),
    )  # fmt: skip
    [(_, summary)] = brain.jobs.settled
    assert summary == "I couldn't check other stores just now. Ask me again in a minute."
    assert not spawned and not said and "offers" not in summary


# ---------------------------------------------------------------- flight check-in
def test_a_check_in_that_never_reached_the_airline_opens_the_airlines_page():
    job = Job(
        agent_id="scout", agent_name="Scout", role="browser", kind="checkin", status="failed",
        task="Check in: Skyward Air SK 482\n...", start_url="https://skyward.example.com/check-in",
        summary="I couldn't open a browser window on this PC. Restarting Pyxi usually fixes it.",
    )  # fmt: skip
    note = paperwork.notice(job, {})
    assert note["link"] == "https://skyward.example.com/check-in" and note["actions"] == ["link"]
    assert note["body"].endswith("Press Open to check in yourself.")
    # What the airline needs from them keeps its own words.
    note = paperwork.notice(job, {"needs_you": "your passport number", "link": "https://skyward.example.com/step2"})
    assert note["link"].endswith("/step2") and "Press Open" not in note["body"]
