
"""Cozy voice listener.

Pipeline:
Microphone -> speech segmentation -> local Whisper transcription
-> wake-word check -> Cozy API -> speech response.

Online speech uses Microsoft Edge TTS. If online speech fails, Cozy
automatically falls back to an installed Windows voice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import sys
import tempfile
import time
from collections import deque
from typing import Callable, Deque, List, Optional

import numpy as np

from . import actions


SAMPLE_RATE = 16000
FRAME = 480  # 30 ms
WHISPER_HINT = (
    "Ok Cozy. Cozy, open YouTube. Cozy, play a song. "
    "Open File Explorer."
)

YES = {
    "yes", "yeah", "yep", "yup", "sure", "approve",
    "approved", "confirm", "ok", "okay", "proceed",
}

NO = {
    "no", "nope", "cancel", "stop", "reject",
    "don't", "dont", "never",
}


class Segmenter:
    """Split microphone audio into utterances using audio energy."""

    def __init__(
        self,
        threshold: float,
        silence_frames: int = 27,
        min_speech_frames: int = 8,
        max_frames: int = 500,
        preroll: int = 10,
    ):
        self.threshold = threshold
        self.silence_frames = silence_frames
        self.min_speech_frames = min_speech_frames
        self.max_frames = max_frames

        self._preroll: Deque[np.ndarray] = deque(maxlen=preroll)
        self._buf: List[np.ndarray] = []
        self._active = False
        self._silence = 0

    @staticmethod
    def rms(frame: np.ndarray) -> float:
        if not frame.size:
            return 0.0

        return float(
            np.sqrt(np.mean(frame.astype(np.float64) ** 2))
        )

    def reset(self) -> None:
        self._preroll.clear()
        self._buf = []
        self._active = False
        self._silence = 0

    def feed(self, frame: np.ndarray) -> Optional[np.ndarray]:
        loud = self.rms(frame) > self.threshold

        if not self._active:
            if not loud:
                self._preroll.append(frame)
                return None

            self._active = True
            self._buf = list(self._preroll) + [frame]
            self._silence = 0
            return None

        self._buf.append(frame)
        self._silence = 0 if loud else self._silence + 1

        if (
            self._silence >= self.silence_frames
            or len(self._buf) >= self.max_frames
        ):
            speech_frames = len(self._buf) - self._silence

            output = (
                np.concatenate(self._buf)
                if speech_frames >= self.min_speech_frames
                else None
            )

            self.reset()
            return output

        return None


class VoiceSession:
    """Handle wake words, commands, and spoken approval responses."""

    def __init__(
        self,
        send: Callable[[str], dict],
        approve: Callable[[str, bool], dict],
        speak: Callable[[str], None],
        now: Callable[[], float] = time.monotonic,
        active_window: float = 8.0,
    ):
        self.send = send
        self.approve = approve
        self.speak = speak
        self.now = now

        self.active_window = active_window
        self.active_until = 0.0
        self.pending: Optional[str] = None
        self._retries = 0

    def handle(self, text: str) -> None:
        text = text.strip()

        if not text:
            return

        # If approval is pending, interpret the next utterance as yes/no.
        if self.pending:
            self._answer_approval(text)
            return

        awake = self.now() < self.active_until

        if not (awake or actions.has_wake_word(text)):
            # Ignore speech that is not intended for Cozy.
            return

        if not actions.strip_wake_word(text):
            self.active_until = self.now() + self.active_window
            self.speak("Yes?")
            return

        self.active_until = 0.0

        try:
            response = self.send(text)
            self._report(response)
        except Exception as exc:
            print(f"(Command request failed: {exc})")
            self.speak(
                "I couldn't reach my local assistant server. "
                "Please check that Cozy is running."
            )

    def _report(self, response: dict) -> None:
        if response.get("status") == "approval_required":
            self.pending = response["approval_id"]
            self._retries = 0

            description = response.get("text", "do that")

            self.speak(
                f"I need your approval to {description}. "
                "Say yes or no."
            )
        else:
            message = (
                response.get("message")
                or response.get("status")
                or "Done."
            )
            self.speak(str(message))

    def _answer_approval(self, text: str) -> None:
        words = set(
            text.lower()
            .replace(",", " ")
            .replace(".", " ")
            .replace("!", " ")
            .replace("?", " ")
            .split()
        )

        approval_id = self.pending

        if words & NO:
            try:
                self.approve(approval_id, False)
            finally:
                self.pending = None

            self.speak("Okay, cancelled.")

        elif words & YES:
            try:
                response = self.approve(approval_id, True)
                note = str(response.get("note", "")).strip()

                message = "Approved."
                if note:
                    message += " " + note

                self.speak(message)

            except Exception as exc:
                print(f"(Approval request failed: {exc})")
                self.speak("I couldn't confirm that approval.")

            finally:
                self.pending = None

        elif self._retries == 0:
            self._retries = 1
            self.speak("Please say yes or no.")

        else:
            try:
                self.approve(approval_id, False)
            except Exception as exc:
                print(f"(Cancellation request failed: {exc})")

            self.pending = None
            self.speak("I'll cancel that for now.")


class Speaker:
    """Speak using Edge Neural TTS with an offline Windows fallback."""

    def __init__(
        self,
        enabled: bool = True,
        provider: str = "windows",
        voice: str = "en-IN-NeerjaNeural",
        rate: str = "-8%",
        volume: str = "+0%",
        pitch: str = "-2Hz",
    ):
        self.enabled = enabled
        self.provider = provider
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch

        self._engine = None
        self._pygame = None

    def _speak_windows(self, text: str) -> None:
        """Speak locally using an installed Windows voice."""

        if self._engine is None:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", 160)
            self._engine.setProperty("volume", 0.88)

            voices = self._engine.getProperty("voices")

            # Prefer an installed female voice, if available.
            female_voice = next(
                (
                    voice
                    for voice in voices
                    if any(
                        name in voice.name.lower()
                        for name in ("zira", "hazel")
                    )
                ),
                None,
            )

            if female_voice is not None:
                self._engine.setProperty(
                    "voice", female_voice.id
                )

        self._engine.say(text)
        self._engine.runAndWait()

    def _speak_edge(self, text: str) -> None:
        """Generate and play online Edge Neural TTS audio."""

        import edge_tts
        import pygame

        if self._pygame is None:
            pygame.mixer.init()
            self._pygame = pygame

        async def synthesize(path: str) -> None:
            communication = edge_tts.Communicate(
                text,
                self.voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )

            await communication.save(path)

        fd, path = tempfile.mkstemp(
            prefix="cozy_tts_",
            suffix=".mp3",
        )
        os.close(fd)

        try:
            # Generate the audio using the online neural voice.
            asyncio.run(synthesize(path))

            if not os.path.exists(path) or os.path.getsize(path) == 0:
                raise RuntimeError("Edge TTS returned an empty audio file.")

            self._pygame.mixer.music.load(path)
            self._pygame.mixer.music.play()

            while self._pygame.mixer.music.get_busy():
                time.sleep(0.05)

            self._pygame.mixer.music.unload()

        finally:
            # Release the temporary file even if synthesis or playback fails.
            try:
                if self._pygame is not None:
                    self._pygame.mixer.music.stop()
                    self._pygame.mixer.music.unload()
            except Exception:
                pass

            try:
                os.remove(path)
            except OSError:
                pass

    def __call__(self, text: str) -> None:
        """Make Speaker callable: speaker('Hello') speaks the text."""

        text = text.strip()

        if not text:
            return

        print(f"Cozy: {text}")

        if not self.enabled:
            return

        if self.provider == "edge":
            try:
                self._speak_edge(text)
                return

            except Exception as exc:
                print(f"(Edge TTS unavailable: {exc})")
                print(
                    "Switching to offline Windows speech..."
                )

                try:
                    self._speak_windows(text)
                    return

                except Exception as fallback_exc:
                    print(
                        "(Windows speech also unavailable: "
                        f"{fallback_exc})"
                    )
                    return

        # Windows-only mode.
        try:
            self._speak_windows(text)

        except Exception as exc:
            print(f"(Speech output unavailable: {exc})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cozy hands-free voice listener"
    )

    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
    )

    parser.add_argument(
        "--model",
        default="base.en",
        help=(
            "Whisper model: tiny.en (fast), base.en, "
            "small.en (more accurate)"
        ),
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=0,
        help="Microphone loudness threshold (0 = auto)",
    )

    parser.add_argument(
        "--no-speak",
        action="store_true",
        help="Print responses without speaking",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print what the microphone hears",
    )

    parser.add_argument(
        "--tts",
        choices=("windows", "edge"),
        default="windows",
        help=(
            "Speech engine: windows (offline) or "
            "edge (online neural voice with Windows fallback)"
        ),
    )

    parser.add_argument(
        "--voice",
        default="en-IN-NeerjaNeural",
        help="Edge Neural TTS voice ID",
    )

    parser.add_argument(
        "--rate",
        default="-8%",
        help="Edge speech rate",
    )

    parser.add_argument(
        "--volume",
        default="+0%",
        help="Edge speech volume",
    )

    parser.add_argument(
        "--pitch",
        default="-2Hz",
        help="Edge speech pitch",
    )

    args = parser.parse_args(argv)

    import httpx
    import sounddevice as sd

    # Check that Cozy's local API is running.
    try:
        httpx.get(
            f"{args.url}/health",
            timeout=3,
        ).raise_for_status()

    except Exception:
        print(
            "Cozy server is not running. "
            "In another terminal run: python -m app.main"
        )
        return 1

    from faster_whisper import WhisperModel

    print(
        f"Loading speech model '{args.model}' "
        "(first run downloads it; then it can work offline)..."
    )

    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
    )

    def send(text: str) -> dict:
        response = httpx.post(
            f"{args.url}/command",
            json={"text": text},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()

    def approve(approval_id: str, approved: bool) -> dict:
        response = httpx.post(
            f"{args.url}/approve/{approval_id}",
            params={"approved": approved},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    speaker = Speaker(
        enabled=not args.no_speak,
        provider=args.tts,
        voice=args.voice,
        rate=args.rate,
        volume=args.volume,
        pitch=args.pitch,
    )

    session = VoiceSession(
        send=send,
        approve=approve,
        speak=speaker,
    )

    frames: queue.Queue[np.ndarray] = queue.Queue()

    def on_audio(indata, frame_count, time_info, status):
        if status:
            print(f"(Microphone status: {status})")

        frames.put(indata[:, 0].copy())

    print(f"TTS provider: {args.tts}")

    if args.tts == "edge":
        print(f"TTS voice: {args.voice}")
        print("Offline Windows speech fallback: enabled")

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            callback=on_audio,
        ):
            print(
                "Calibrating to room noise; stay quiet "
                "for 2 seconds..."
            )

            levels = [
                Segmenter.rms(frames.get())
                for _ in range(66)
            ]

            threshold = args.threshold or max(
                250.0,
                float(np.percentile(levels, 90)) * 2.5 + 100,
            )

            segmenter = Segmenter(threshold)

            print(
                "Ready. Say 'OK Cozy, open YouTube'. "
                f"(threshold {threshold:.0f}; Ctrl+C to quit)"
            )

            while True:
                frame = frames.get()
                utterance = segmenter.feed(frame)

                if utterance is None:
                    continue

                audio = utterance.astype(np.float32) / 32768.0

                segments, _ = model.transcribe(
                    audio,
                    language="en",
                    beam_size=1,
                    initial_prompt=WHISPER_HINT,
                    vad_filter=False,
                )

                text = " ".join(
                    segment.text.strip()
                    for segment in segments
                ).strip()

                if args.debug:
                    print(f"[heard] {text!r}")

                if text:
                    session.handle(text)

                # Discard audio captured while processing or speaking.
                while not frames.empty():
                    try:
                        frames.get_nowait()
                    except queue.Empty:
                        break

                segmenter.reset()

    except KeyboardInterrupt:
        print("\nCozy voice listener stopped.")

    except Exception as exc:
        print(f"\nVoice listener error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
