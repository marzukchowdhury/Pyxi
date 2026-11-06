"""Keys and tokens on screen are hidden from the model and blacked out of the screenshot."""

import pytest
from PIL import Image

from pyxi.screen import secrets
from pyxi.screen.context import ScreenReader, UIElement
from pyxi.screen.ocr import TextLine

FAKE = "x" * 4  # assembled at runtime so no real-looking key sits in the repo


@pytest.mark.parametrize(
    "text",
    [
        "OPENROUTER_API_KEY=sk-or-v1-" + "a1b2c3d4" * 6,
        "sk-ant-api03-" + "Z9" * 20,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "token: ghp_" + "A" * 36,
        "client_secret = 'Abcdef123456789'",
        "pyxi_" + "0f" * 24,
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_credentials_are_spotted(text):
    assert secrets.looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Dark mode",
        "Export settings",
        "The secret to a good pizza is the dough",
        "Password",
        f"sk-{FAKE}",
        "Reset token count",
    ],
)
def test_ordinary_text_is_left_alone(text):
    assert not secrets.looks_secret(text)


def test_the_spot_is_blacked_out_and_the_text_dropped():
    shot = Image.new("RGB", (400, 200), (255, 255, 255))
    key_line = TextLine(text="OPENROUTER_API_KEY=sk-or-v1-" + "ab12" * 12, rect=(110, 60, 200, 20))
    ok_line = TextLine(text="Untitled - Notepad", rect=(10, 10, 120, 16))
    field = UIElement(1, "token=" + "Q" * 30, "Edit", (110, 120, 150, 24))
    kept = ScreenReader._hide_secrets(shot, (100, 50), [field], [key_line, ok_line])
    assert kept == [ok_line] and field.name == secrets.HIDDEN
    assert shot.getpixel((10 + 100, 10 + 10)) == (20, 20, 20)  # inside the key line (local coords)
    assert shot.getpixel((5, 5)) == (255, 255, 255)
