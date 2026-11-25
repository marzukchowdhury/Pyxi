"""The sound the PC is playing, or the microphone, as 16 kHz mono samples in memory.

Windows only, through WASAPI in shared mode: "loopback" on the default speakers gives what
they play, with no extra driver and no admin rights. Nothing is written to disk.

Things Windows does that this handles:

* **Nothing playing.** A loopback stream delivers no packets at all while no program plays
  sound. Silence is filled in by the clock, so whoever listens still sees time pass (a caption
  ends, a meeting's timestamps stay right).
* **The device changes half way** (headphones plugged in, a dock unplugged): the default
  device is checked about once a second and the stream is opened again on the new one.
* **The PC is muted.** Checked on a real PC (Windows 11, its built-in speakers): the
  loopback stream still carried the full signal while the speakers were muted (peak 0.73 of
  full scale), because it is taken before the volume control. So captions and notes work
  with the sound off, and a meeting is still heard when they mute the PC. Some sound cards
  do their volume in software, before the loopback: there a muted PC is heard as silence,
  which the listeners treat like any silence (:func:`muted` tells the two apart).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

RATE = 16000
SYSTEM, MIC = "system", "mic"

_LOOPBACK = 0x00020000  # AUDCLNT_STREAMFLAGS_LOOPBACK
_SILENT = 0x2  # AUDCLNT_BUFFERFLAGS_SILENT
_CLSCTX_ALL = 23
_FLOAT, _PCM, _EXTENSIBLE = 3, 1, 0xFFFE
_GAP_S = 0.1  # this long without a packet is filled with silence
_DEVICE_CHECK_S = 1.0

Sink = Callable[[Any, float], None]  # (float32 samples at 16 kHz, monotonic time of their end)


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import comtypes  # noqa: F401
        import pycaw  # noqa: F401
    except ImportError:
        return False
    return True


def _interfaces() -> tuple[Any, Any, Any, Any]:
    """The Core Audio interfaces (pycaw has all but the capture client)."""
    from ctypes import HRESULT, POINTER, c_ubyte, c_uint32, c_uint64
    from ctypes.wintypes import DWORD

    from comtypes import COMMETHOD, GUID, IUnknown
    from pycaw.api.audioclient import IAudioClient
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator

    global _CAPTURE_CLIENT
    if _CAPTURE_CLIENT is None:

        class IAudioCaptureClient(IUnknown):
            _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
            _methods_ = (
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetBuffer",
                    (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
                    (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                    (["out"], POINTER(DWORD), "pdwFlags"),
                    (["in"], POINTER(c_uint64), "pu64DevicePosition"),
                    (["in"], POINTER(c_uint64), "pu64QPCPosition"),
                ),
                COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetNextPacketSize",
                    (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
                ),
            )

        _CAPTURE_CLIENT = IAudioCaptureClient
    return IMMDeviceEnumerator, IAudioClient, _CAPTURE_CLIENT, GUID


_CAPTURE_CLIENT: Any = None
_ENUMERATOR_CLSID = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_FLOAT_SUBTYPE = "{00000003-0000-0010-8000-00AA00389B71}"


def _enumerator() -> Any:
    import comtypes

    enumerator, _, _, guid = _interfaces()
    return comtypes.CoCreateInstance(guid(_ENUMERATOR_CLSID), enumerator, _CLSCTX_ALL)


def _default_device(source: str) -> Any:
    flow = 0 if source == SYSTEM else 1  # eRender / eCapture
    enum = _enumerator()
    if source == MIC:
        try:  # the microphone calls use, when they have set one apart
            return enum.GetDefaultAudioEndpoint(flow, 2)  # eCommunications
        except Exception:
            pass
    return enum.GetDefaultAudioEndpoint(flow, 0)  # eConsole


def muted() -> bool | None:
    """Are the speakers muted (or at zero)? None when Windows won't say."""
    if not available():
        return None
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities

        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        volume = AudioUtilities.GetSpeakers().EndpointVolume
        return bool(volume.GetMute()) or float(volume.GetMasterVolumeLevelScalar()) <= 0.005
    except Exception:
        return None


class _Format:
    """What the device's mix format means for turning its bytes into samples."""

    def __init__(self, fmt: Any) -> None:
        import ctypes

        self.channels = int(fmt.nChannels)
        self.rate = int(fmt.nSamplesPerSec)
        self.bits = int(fmt.wBitsPerSample)
        self.block = int(fmt.nBlockAlign)
        tag = int(fmt.wFormatTag)
        if tag == _EXTENSIBLE and fmt.cbSize >= 22:
            # WAVEFORMATEXTENSIBLE: the sub format's GUID sits 24 bytes in (after the 18 byte
            # WAVEFORMATEX, the valid bits and the channel mask).
            from comtypes import GUID

            sub = GUID.from_address(ctypes.addressof(fmt) + 24)
            tag = _FLOAT if str(sub).upper() == _FLOAT_SUBTYPE else _PCM
        self.float = tag == _FLOAT

    def mono(self, raw: bytes) -> Any:
        """Interleaved bytes from the device -> mono float32 at the device's own rate."""
        import numpy as np

        if self.float and self.bits == 32:
            data = np.frombuffer(raw, dtype=np.float32)
        elif self.bits == 16:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif self.bits == 32:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif self.bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            data = (v - ((v & 0x800000) << 1)).astype(np.float32) / 8388608.0
        else:
            raise ValueError(f"a sound format Pyxi can't read ({self.bits} bit)")
        if self.channels > 1:
            data = data[: len(data) // self.channels * self.channels]
            data = data.reshape(-1, self.channels).mean(axis=1)
        return np.ascontiguousarray(data, dtype=np.float32)


class Resampler:
    """Mono float32 at any rate -> 16 kHz, keeping its place between chunks.

    Windowed-sinc filtering in numpy (the mix rate is nearly always 48 or 44.1 kHz), so
    nothing beyond what Pyxi already ships is needed and there is no per-chunk click.
    """

    TAPS = 48

    def __init__(self, rate: int, out_rate: int = RATE) -> None:
        import numpy as np

        self.rate, self.out_rate = rate, out_rate
        self.step = rate / out_rate
        self._pos = 0.0  # where the next output sample falls, in input samples
        self._tail = np.zeros(0, dtype=np.float32)
        cutoff = min(1.0, out_rate / rate) * 0.92  # of the input's Nyquist
        n = np.arange(-self.TAPS, self.TAPS + 1, dtype=np.float64)
        kernel = np.sinc(n * cutoff) * cutoff * np.hamming(len(n))
        self._kernel = (kernel / kernel.sum()).astype(np.float32)

    def feed(self, chunk: Any) -> Any:
        import numpy as np

        if self.rate == self.out_rate:
            return chunk
        data = np.concatenate([self._tail, chunk]) if len(self._tail) else chunk
        if len(data) <= 2 * self.TAPS:
            self._tail = data
            return np.zeros(0, dtype=np.float32)
        smooth = np.convolve(data, self._kernel, mode="valid")  # smooth[i] is data[i + TAPS]
        last = len(smooth) - 1
        count = int((last - self._pos) // self.step) + 1 if last >= self._pos else 0
        if count <= 0:
            self._tail = data
            return np.zeros(0, dtype=np.float32)
        at = self._pos + self.step * np.arange(count)
        lo = np.floor(at).astype(np.int64)
        frac = (at - lo).astype(np.float32)
        hi = np.minimum(lo + 1, last)
        out = smooth[lo] * (1.0 - frac) + smooth[hi] * frac
        nxt = self._pos + self.step * count  # in smooth's (and so data's) own count
        keep = int(nxt)  # the first input sample still needed
        self._tail = data[keep:]
        self._pos = nxt - keep
        return out.astype(np.float32)


class Capture:
    """One running capture (the speakers' loopback, or the microphone), on its own thread.

    ``sink(samples, t)`` is called on that thread with every chunk; ``problem`` holds a plain
    sentence when it could not start or had to stop.
    """

    def __init__(self, source: str, sink: Sink) -> None:
        self.source = source
        self.sink = sink
        self.problem = ""
        self.level = 0.0  # the latest chunk's peak, 0 to 1 (for the sign)
        self.device = ""  # the device's id, so a change can be noticed
        self.reopened = 0  # how often the device changed under it
        self.heard_at = 0.0  # monotonic time real sound (not filled silence) last arrived
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ control
    def start(self, wait_s: float = 3.0) -> bool:
        """Start capturing. False (with ``problem`` set) when the device can't be opened."""
        if self._thread is not None:
            return not self.problem
        self._thread = threading.Thread(
            target=self._run, name=f"pyxi-listen-{self.source}", daemon=True
        )
        self._thread.start()
        self._ready.wait(wait_s)
        return not self.problem

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    # ------------------------------------------------------------------ the thread
    def _open(self) -> tuple[Any, Any, _Format, Resampler]:
        _, audio_client, capture_client, _ = _interfaces()
        device = _default_device(self.source)
        self.device = str(device.GetId())
        client = device.Activate(audio_client._iid_, _CLSCTX_ALL, None).QueryInterface(audio_client)
        mix = client.GetMixFormat()
        fmt = _Format(mix.contents)
        flags = _LOOPBACK if self.source == SYSTEM else 0
        client.Initialize(0, flags, 2_000_000, 0, mix, None)  # shared mode, a 200 ms buffer
        capture = client.GetService(capture_client._iid_).QueryInterface(capture_client)
        client.Start()
        return client, capture, fmt, Resampler(fmt.rate)

    def _current_device(self) -> str:
        try:
            return str(_default_device(self.source).GetId())
        except Exception:
            return ""

    def _run(self) -> None:
        import ctypes

        import comtypes
        import numpy as np

        try:
            comtypes.CoInitializeEx(0)  # this thread's own COM apartment
        except OSError:
            pass
        client = capture = None
        try:
            try:
                client, capture, fmt, resampler = self._open()
            except Exception as exc:
                self.problem = _plain(self.source, exc)
                log.warning("couldn't open the %s capture: %s", self.source, exc)
                return
            finally:
                self._ready.set()
            last_packet = last_check = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                try:
                    got = False
                    while capture.GetNextPacketSize() > 0:
                        data, frames, flags = capture.GetBuffer(None, None)
                        size = int(frames) * fmt.block
                        if flags & _SILENT or not size:
                            chunk = np.zeros(int(frames), dtype=np.float32)
                        else:
                            chunk = fmt.mono(ctypes.string_at(data, size))
                        capture.ReleaseBuffer(frames)
                        got = True
                        self._deliver(resampler.feed(chunk), real=True)
                    if got:
                        last_packet = time.monotonic()
                    elif now - last_packet >= _GAP_S:
                        # Nothing is playing: Windows sends nothing. Time still passes.
                        gap = now - last_packet
                        last_packet = now
                        self._deliver(np.zeros(int(gap * RATE), dtype=np.float32), real=False)
                    changed = False
                    if now - last_check >= _DEVICE_CHECK_S:
                        last_check = now
                        current = self._current_device()
                        changed = bool(current) and current != self.device
                except Exception as exc:  # unplugged, or the sound service restarted
                    log.info("the %s device went away (%s); opening it again", self.source, exc)
                    changed = True
                if changed:
                    _close(client)
                    client = capture = None
                    for _ in range(40):  # a new default device takes a moment to settle
                        if self._stop.is_set():
                            return
                        try:
                            client, capture, fmt, resampler = self._open()
                            self.reopened += 1
                            last_packet = time.monotonic()
                            break
                        except Exception:
                            # No device at all for now (every output unplugged): keep time.
                            self._deliver(np.zeros(RATE // 4, dtype=np.float32), real=False)
                            time.sleep(0.25)
                    else:
                        self.problem = _plain(self.source, None)
                        return
                    continue
                time.sleep(0.01)
        finally:
            _close(client)
            self._ready.set()
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _deliver(self, samples: Any, *, real: bool) -> None:
        if not len(samples):
            return
        now = time.monotonic()
        if real:
            peak = float(abs(samples).max())
            self.level = peak
            if peak > 0.003:
                self.heard_at = now
        else:
            self.level = 0.0
        try:
            self.sink(samples, now)
        except Exception:
            log.exception("a listener of the %s capture failed", self.source)


def _close(client: Any) -> None:
    if client is None:
        return
    try:
        client.Stop()
    except Exception:
        pass


def _plain(source: str, exc: BaseException | None) -> str:
    if source == MIC:
        return (
            "I couldn't open the microphone. Check that one is plugged in and that Windows "
            "lets desktop apps use it (Settings, Privacy & security, Microphone)."
        )
    return (
        "I couldn't listen to this PC's sound: Windows has no speakers or headphones to "
        "listen to right now."
    )
