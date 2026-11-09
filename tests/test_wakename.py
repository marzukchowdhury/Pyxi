from pyxi.wakename import normalize


def test_misheard_greetings_become_pyxi() -> None:
    heard = {
        "Your pixxit split my left screen, Facebook on the left": "Pyxi, split my left screen, Facebook on the left",
        "Epic City can you split the screen": "Pyxi, can you split the screen",
        "Hey Pixie, can you set an alarm for 7": "Pyxi, can you set an alarm for 7",
        "Hello, Pixi, can you see that?": "Pyxi, can you see that?",
        "Hey Pixig, show me in 3D": "Pyxi, show me in 3D",
    }
    for said, meant in heard.items():
        assert normalize(said) == meant, said


def test_ordinary_sentences_are_left_alone() -> None:
    for said in ("Open Gmail.", "A pixie lives in the garden", "Your pixel art looks great", "Hey, what time is it?"):
        assert normalize(said) == said
