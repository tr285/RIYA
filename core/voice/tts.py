"""Local offline Text-to-Speech abstraction for RIYA."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from core.ai.safety import strip_unsafe_control_chars
from core.voice.errors import TTSError

_SAY_BIN = "/usr/bin/say"
_VOICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,63}$")
_LIST_LINE_RE = re.compile(r"^(?P<name>.+?)\s{2,}(?P<locale>[a-z]{2}_[A-Z]{2})\s+#\s*(?P<sample>.*)$")


class LocalTTS:
    """Local offline Text-to-Speech engine backed by macOS ``/usr/bin/say``.

    Honest capability declaration:
    Apple's ``/usr/bin/say`` supports voice selection and speech rate (``-r``),
    not expressive neural emotional prosody (``supports_emotional_prosody = False``).
    """

    engine_name = "macos_say"
    supports_wav_synthesis = True
    supports_emotional_prosody = False
    supports_rate_control = True

    def __init__(
        self,
        voice: str = "Samantha",
        *,
        rate_wpm: int | None = None,
        volume: float = 1.0,
        timeout_seconds: float = 30.0,
    ):
        if not isinstance(voice, str) or not _VOICE_PATTERN.fullmatch(voice.strip()):
            raise ValueError("Invalid TTS voice name.")
        if rate_wpm is not None:
            if isinstance(rate_wpm, bool) or not isinstance(rate_wpm, int) or not (90 <= rate_wpm <= 360):
                raise ValueError("rate_wpm must be an integer between 90 and 360.")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not (0.0 <= volume <= 1.0):
            raise ValueError("volume must be between 0.0 and 1.0.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive.")

        self.voice = voice.strip()
        self.rate_wpm = rate_wpm
        self.volume = float(volume)
        self.timeout_seconds = float(timeout_seconds)

    def _sanitize_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        return strip_unsafe_control_chars(text, allow_newlines=True)

    def _build_say_command(
        self,
        *,
        rate_wpm: int | None = None,
        output_wav: Path | None = None,
    ) -> list[str]:
        cmd = [_SAY_BIN, "-v", self.voice]
        effective_rate = self.rate_wpm if rate_wpm is None else rate_wpm
        if effective_rate is not None:
            if (
                isinstance(effective_rate, bool)
                or not isinstance(effective_rate, int)
                or not (90 <= effective_rate <= 360)
            ):
                raise ValueError("rate_wpm must be between 90 and 360.")
            cmd.extend(["-r", str(effective_rate)])
        if output_wav is not None:
            cmd.extend(["-o", str(output_wav), "--data-format=LEI16@22050"])
        cmd.extend(["-f", "-"])
        return cmd

    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        """Speak text synchronously through ``/usr/bin/say`` via stdin."""

        safe_text = self._sanitize_text(text)
        if not safe_text.strip():
            return

        cmd = self._build_say_command(rate_wpm=rate_wpm)
        # Preserve integer 30 timeout default when constructed with default timeout_seconds.
        timeout = 30 if self.timeout_seconds == 30.0 else self.timeout_seconds
        try:
            subprocess.run(
                cmd,
                input=safe_text,
                text=True,
                shell=False,
                check=True,
                timeout=timeout,
            )
        except Exception as exc:
            raise TTSError(f"Local TTS execution failed: {exc}") from exc

    def synthesize_to_wav(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        """Render speech text offline into a 16-bit PCM WAV file for buffered playback."""

        safe_text = self._sanitize_text(text)
        if not safe_text.strip():
            raise ValueError("Cannot synthesize empty text.")
        if not isinstance(output_path, (str, Path)):
            raise ValueError("output_path must be a string or Path.")
        raw_path = os.fspath(output_path)
        if not raw_path or "\x00" in raw_path:
            raise ValueError("Invalid WAV output path.")

        target = Path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_say_command(rate_wpm=rate_wpm, output_wav=target)
        try:
            subprocess.run(
                cmd,
                input=safe_text,
                text=True,
                shell=False,
                check=True,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
            raise TTSError(f"Failed to synthesize WAV speech: {exc}") from exc

        if not target.is_file():
            raise TTSError("TTS synthesizer did not produce an output file.")
        return target

    def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        """Alias for synthesize_to_wav satisfying SpeechSynthesisAdapter contract."""
        return self.synthesize_to_wav(text, output_path, rate_wpm=rate_wpm)

    @classmethod
    def list_available_voices(cls) -> list[str]:
        """Return names of locally installed macOS speech voices."""
        return [v["name"] for v in cls.list_voices() if "name" in v]

    @staticmethod
    def list_voices() -> list[dict[str, str]]:
        """Return locally installed macOS speech voices, or [] if unavailable."""

        if not os.path.isfile(_SAY_BIN):
            return []
        try:
            completed = subprocess.run(
                [_SAY_BIN, "-v", "?"],
                capture_output=True,
                text=True,
                shell=False,
                check=True,
                timeout=5,
            )
        except Exception:
            return []

        voices: list[dict[str, str]] = []
        for line in completed.stdout.splitlines():
            match = _LIST_LINE_RE.match(line.strip())
            if match:
                voices.append(match.groupdict())
        return voices


MacOSTTS = LocalTTS


class LinuxTTS:
    """Local offline Text-to-Speech engine for Linux environments.

    Evaluates supported Linux speech engines in order of priority:
    1. piper-tts (neural local TTS producing natural speech)
    2. espeak-ng / espeak (lightweight multi-lingual speech synthesizer)
    3. spd-say (standard Linux speech-dispatcher utility)
    """

    engine_name = "linux_tts"
    supports_wav_synthesis = True
    supports_emotional_prosody = False
    supports_rate_control = True

    def __init__(
        self,
        voice: str = "default",
        *,
        backend: str = "auto",
        rate_wpm: int | None = None,
        volume: float = 1.0,
        timeout_seconds: float = 30.0,
    ):
        if rate_wpm is not None:
            if isinstance(rate_wpm, bool) or not isinstance(rate_wpm, int) or not (90 <= rate_wpm <= 360):
                raise ValueError("rate_wpm must be an integer between 90 and 360.")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not (0.0 <= volume <= 1.0):
            raise ValueError("volume must be between 0.0 and 1.0.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive.")

        self.voice = str(voice or "default").strip()
        self.rate_wpm = rate_wpm
        self.volume = float(volume)
        self.timeout_seconds = float(timeout_seconds)
        self.backend = backend
        self._selected_backend = self._detect_backend()

    def _detect_backend(self) -> str:
        import shutil

        if self.backend != "auto":
            return self.backend

        if shutil.which("piper"):
            return "piper"
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        if shutil.which("espeak"):
            return "espeak"
        if shutil.which("spd-say"):
            return "spd-say"
        return "none"

    def is_available(self) -> bool:
        return self._selected_backend != "none"

    def _sanitize_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        return strip_unsafe_control_chars(text, allow_newlines=True)

    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        """Speak text synchronously using available Linux speech backend."""
        safe_text = self._sanitize_text(text)
        if not safe_text.strip():
            return

        import shutil
        backend = self._selected_backend
        if backend == "none":
            raise TTSError(
                "No local Linux speech synthesis engine installed. "
                "Install espeak-ng ('sudo apt install espeak-ng') or piper-tts."
            )

        effective_rate = self.rate_wpm if rate_wpm is None else rate_wpm
        rate = effective_rate or 175

        try:
            if backend in {"espeak-ng", "espeak"}:
                bin_name = shutil.which(backend) or backend
                cmd = [bin_name, "-s", str(rate)]
                if self.voice and self.voice != "default":
                    cmd.extend(["-v", self.voice])
                cmd.append(safe_text)
                subprocess.run(
                    cmd,
                    shell=False,
                    check=True,
                    timeout=self.timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            elif backend == "spd-say":
                bin_name = shutil.which("spd-say") or "spd-say"
                # Map rate 90-360 to spd-say range -100 to +100
                spd_rate = max(-100, min(100, int((rate - 175) / 1.5)))
                cmd = [bin_name, "-r", str(spd_rate), safe_text]
                subprocess.run(
                    cmd,
                    shell=False,
                    check=True,
                    timeout=self.timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            elif backend == "piper":
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                    tmp_path = Path(tmp_wav.name)
                try:
                    self.synthesize_to_wav(safe_text, tmp_path, rate_wpm=rate_wpm)
                    # Play temporary WAV
                    aplay = shutil.which("aplay") or shutil.which("paplay")
                    if aplay:
                        subprocess.run([aplay, str(tmp_path)], check=True, timeout=self.timeout_seconds)
                finally:
                    if tmp_path.exists():
                        tmp_path.unlink()
        except Exception as exc:
            raise TTSError(f"Linux TTS execution failed ({backend}): {exc}") from exc

    def synthesize_to_wav(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        """Synthesize text to a 16-bit PCM WAV file on Linux."""
        safe_text = self._sanitize_text(text)
        if not safe_text.strip():
            raise ValueError("Cannot synthesize empty text.")
        if not isinstance(output_path, (str, Path)):
            raise ValueError("output_path must be a string or Path.")
        raw_path = os.fspath(output_path)
        if not raw_path or "\x00" in raw_path:
            raise ValueError("Invalid WAV output path.")

        target = Path(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        import shutil
        backend = self._selected_backend
        if backend == "none":
            raise TTSError("No supported Linux speech synthesizer installed for WAV generation.")

        effective_rate = self.rate_wpm if rate_wpm is None else rate_wpm
        rate = effective_rate or 175

        try:
            if backend in {"espeak-ng", "espeak"}:
                bin_name = shutil.which(backend) or backend
                cmd = [bin_name, "-w", str(target), "-s", str(rate)]
                if self.voice and self.voice != "default":
                    cmd.extend(["-v", self.voice])
                cmd.append(safe_text)
                subprocess.run(
                    cmd,
                    shell=False,
                    check=True,
                    timeout=self.timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            elif backend == "piper":
                bin_name = shutil.which("piper") or "piper"
                cmd = [bin_name, "--output_file", str(target)]
                subprocess.run(
                    cmd,
                    input=safe_text,
                    text=True,
                    shell=False,
                    check=True,
                    timeout=self.timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            else:
                # Fallback via espeak if available
                bin_name = shutil.which("espeak-ng") or shutil.which("espeak")
                if bin_name:
                    subprocess.run(
                        [bin_name, "-w", str(target), "-s", str(rate), safe_text],
                        shell=False,
                        check=True,
                        timeout=self.timeout_seconds,
                    )
                else:
                    raise TTSError("Direct WAV synthesis requires piper or espeak-ng on Linux.")
        except Exception as exc:
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            raise TTSError(f"Failed to synthesize Linux WAV speech: {exc}") from exc

        if not target.is_file():
            raise TTSError("Linux TTS synthesizer did not produce an output file.")
        return target

    def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        return self.synthesize_to_wav(text, output_path, rate_wpm=rate_wpm)

    @classmethod
    def list_available_voices(cls) -> list[str]:
        import shutil
        bin_name = shutil.which("espeak-ng") or shutil.which("espeak")
        if not bin_name:
            return ["default"]
        try:
            res = subprocess.run([bin_name, "--voices"], capture_output=True, text=True, timeout=5)
            voices = []
            for line in res.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    voices.append(parts[3])
            return voices or ["default"]
        except Exception:
            return ["default"]


class WindowsTTS:
    """Windows offline speech synthesizer supporting SAPI 5, PowerShell, and Piper."""

    def __init__(
        self,
        voice: str = "default",
        rate_wpm: int | None = None,
        volume: float = 1.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.voice = voice or "default"
        self.rate_wpm = rate_wpm or 175
        self.volume = max(0.0, min(1.0, volume))
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        import shutil
        import sys
        if sys.platform == "win32":
            return True
        return bool(shutil.which("powershell") or shutil.which("piper") or shutil.which("espeak-ng"))

    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        safe_text = sanitize_tts_text(text)
        if not safe_text:
            return

        import shutil
        import subprocess

        # Map rate WPM (typically 120-250) to SAPI rate (-10 to 10)
        wpm = rate_wpm or self.rate_wpm
        sapi_rate = max(-10, min(10, int((wpm - 175) / 10)))
        sapi_volume = int(self.volume * 100)

        # 1. PowerShell SAPI
        ps_cmd = (
            f"Add-Type -AssemblyName System.Speech; "
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {sapi_rate}; $s.Volume = {sapi_volume}; "
        )
        if self.voice and self.voice != "default":
            safe_voice = self.voice.replace("'", "''")
            ps_cmd += f"try {{ $s.SelectVoice('{safe_voice}') }} catch {{}}; "
        safe_ps_text = safe_text.replace("'", "''")
        ps_cmd += f"$s.Speak('{safe_ps_text}');"

        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                shell=False,
                check=True,
                timeout=self.timeout_seconds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            return
        except Exception as exc:
            # Fallback to piper or espeak
            if shutil.which("piper"):
                bin_name = shutil.which("piper") or "piper"
                subprocess.run(
                    [bin_name],
                    input=safe_text,
                    text=True,
                    shell=False,
                    check=True,
                    timeout=self.timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                return
            raise TTSError(f"Failed to speak via Windows TTS: {exc}") from exc

    def synthesize_to_wav(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        safe_text = sanitize_tts_text(text)
        if not safe_text:
            raise TTSError("Cannot synthesize empty text.")

        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        wpm = rate_wpm or self.rate_wpm
        sapi_rate = max(-10, min(10, int((wpm - 175) / 10)))
        sapi_volume = int(self.volume * 100)

        safe_target = str(target).replace("'", "''")
        safe_ps_text = safe_text.replace("'", "''")

        ps_cmd = (
            f"Add-Type -AssemblyName System.Speech; "
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {sapi_rate}; $s.Volume = {sapi_volume}; "
            f"$s.SetOutputToWaveFile('{safe_target}'); "
        )
        if self.voice and self.voice != "default":
            safe_voice = self.voice.replace("'", "''")
            ps_cmd += f"try {{ $s.SelectVoice('{safe_voice}') }} catch {{}}; "
        ps_cmd += f"$s.Speak('{safe_ps_text}'); $s.SetOutputToNull();"

        import subprocess
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                shell=False,
                check=True,
                timeout=self.timeout_seconds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            raise TTSError(f"Failed to synthesize Windows WAV speech: {exc}") from exc

        if not target.is_file():
            raise TTSError("Windows TTS synthesizer did not produce an output file.")
        return target

    def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        return self.synthesize_to_wav(text, output_path, rate_wpm=rate_wpm)

    @classmethod
    def list_available_voices(cls) -> list[str]:
        import subprocess
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            voices = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return voices or ["default"]
        except Exception:
            return ["default"]


class TTSFactory:
    """Factory creating appropriate local TTS engine based on platform."""

    @staticmethod
    def create_tts_engine(
        platform_name: str | None = None,
        *,
        voice: str | None = None,
        rate_wpm: int | None = None,
        volume: float = 1.0,
        timeout_seconds: float = 30.0,
        **kwargs: Any,
    ) -> LocalTTS | LinuxTTS | WindowsTTS:
        import sys
        target = platform_name.lower() if platform_name else (
            "windows" if sys.platform == "win32" else ("linux" if sys.platform.startswith("linux") else "macos")
        )
        if target == "windows":
            return WindowsTTS(
                voice=voice or "default",
                rate_wpm=rate_wpm,
                volume=volume,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        elif target == "linux":
            return LinuxTTS(
                voice=voice or "default",
                rate_wpm=rate_wpm,
                volume=volume,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        return LocalTTS(
            voice=voice or "Samantha",
            rate_wpm=rate_wpm,
            volume=volume,
            timeout_seconds=timeout_seconds,
        )

