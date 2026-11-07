"""brain.log without personal data: emails, phone numbers, names in paths and tokens are
scrubbed as each line is written."""

from __future__ import annotations

import io
import logging
import sys

from pyxi import logscrub
from pyxi.logscrub import ScrubbingStream, scrub


def test_personal_data_and_secrets_are_replaced():
    line = (
        r"WARNING pyxi.files: couldn't read C:\Users\Taysseer\Documents\Lease.pdf for "
        "sam.rahman@uni.edu (call (604) 555-0199 or +44 20 7946 0958) with key sk-or-v1-"
        + "f" * 40
        + " and Bearer pyxi_"
        + "0" * 48
    )
    out = scrub(line)
    assert r"C:\Users\<user>\Documents\Lease.pdf" in out
    assert "<email>" in out and "sam.rahman" not in out
    assert out.count("<phone>") == 2 and "555-0199" not in out
    assert "sk-or-v1" not in out and "pyxi_000" not in out and "[hidden]" in out
    assert scrub("file:///c:/users/omen/desktop/x and /home/alex/.config") == (
        "file:///c:/users/<user>/desktop/x and /home/<user>/.config"
    )


def test_ordinary_log_lines_are_untouched():
    for line in [
        "INFO uvicorn: 127.0.0.1:55012 - GET /api/health 200",
        "hands step 3: click [12] Export at (1024, 768)",
        "2026-11-07 12:30:05 voice model ready (Pyxi 0.1.6)",
        "job job_3f2a done in 12.5 s, $0.0031",
    ]:
        assert scrub(line) == line


def test_the_launch_key_is_hidden(monkeypatch):
    monkeypatch.setenv("PYXI_BRAIN_KEY", "Zm9vYmFyYmF6cXV4cXV1eDEyMzQ1Ng")
    assert scrub("ws://127.0.0.1:8766/ws?key=Zm9vYmFyYmF6cXV4cXV1eDEyMzQ1Ng") == "ws://127.0.0.1:8766/ws?key=[hidden]"


def test_the_stream_scrubs_whole_lines_and_tracebacks():
    sink = io.StringIO()
    stream = ScrubbingStream(sink)
    stream.write("partial alex@exa")
    assert sink.getvalue() == ""  # held until the line is complete, so nothing slips through split
    stream.write("mple.com done\nnext ")
    stream.flush()
    assert sink.getvalue() == "partial <email> done\nnext "
    handler = logging.StreamHandler(stream)
    log = logging.getLogger("pyxi.test.scrub")
    log.addHandler(handler)
    try:
        try:
            raise RuntimeError(r"can't open C:\Users\Alex\secret.txt")
        except RuntimeError:
            log.exception("failed for bob@example.org")
    finally:
        log.removeHandler(handler)
    stream.flush()
    text = sink.getvalue()
    assert "bob@" not in text and r"C:\Users\<user>\secret.txt" in text and "Traceback" in text


def test_install_wraps_stdout_and_stderr(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    logscrub.install()
    logscrub.install()  # twice is harmless
    assert isinstance(sys.stdout, ScrubbingStream) and isinstance(sys.stderr, ScrubbingStream)
    assert not isinstance(sys.stdout.inner, ScrubbingStream)
    print("mail me at a@b.co")
    sys.stdout.flush()
    assert sys.stdout.inner.getvalue() == "mail me at <email>\n"
