"""Listening to what the PC plays (and, for meetings, the microphone), as text.

* :mod:`pyxi.listen.capture`: the sound itself, from Windows (WASAPI), in memory only.
* :mod:`pyxi.listen.segment`: where one stretch of speech ends and the next begins.
* :mod:`pyxi.listen.live`: the two together with the speech model Pyxi already has.
* :mod:`pyxi.listen.meeting`: meeting notes with no bot in the call.
* :mod:`pyxi.listen.captions`: live captions and translation over anything playing.
* :mod:`pyxi.listen.wincaptions`: Windows 11's own Live Captions as a source of the words.

Nothing here starts by itself: the brain starts it when they ask, in that moment
(:mod:`pyxi.companion.listening`), and a sign with a Stop button is on screen while it runs.
"""
