import os
import shutil
import threading
import logging
import subprocess
import queue
import time
import uuid
from utils.common import async_runner
from config.settings import Config

try:
    import speech_recognition as sr
    import pygame
    SR_AVAILABLE = True
except ImportError:
    SR_AVAILABLE = False
    sr = None
    pygame = None

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    gTTS = None

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    WhisperModel = None

try:
    from piper.voice import PiperVoice
    PIPER_PY_AVAILABLE = True
except ImportError:
    PIPER_PY_AVAILABLE = False
    PiperVoice = None

log = logging.getLogger("elara.voice")

class VoiceService:
    """Speech-to-text + text-to-speech service with threaded workers."""

    def __init__(self, elara_skill):
        self.elara = elara_skill
        self.recognizer = sr.Recognizer() if SR_AVAILABLE else None
        self.stt_engine = (Config.VOICE_STT_ENGINE or "google").lower()
        self.tts_engine = (Config.VOICE_TTS_ENGINE or "gtts").lower()
        self.whisper_model = None
        self.piper_voice = None
        self._tts_queue = queue.Queue()
        self._tts_thread = None
        self._listen_thread = None
        self._watchdog_thread = None
        self._stop_event = threading.Event()
        self._mixer_lock = threading.Lock()
        self._mixer_initialized = False
        self._watchdog_interval_sec = max(2, int(getattr(Config, "VOICE_WATCHDOG_INTERVAL_SEC", 5)))
        self._last_stt_error_log = 0.0
        self._init_engines()

    def _init_engines(self):
        if self.stt_engine == "whisper":
            if not WHISPER_AVAILABLE:
                log.warning("Faster-Whisper not available. Falling back to Google STT.")
                self.stt_engine = "google"
            else:
                try:
                    self.whisper_model = WhisperModel(
                        Config.WHISPER_MODEL,
                        device=Config.WHISPER_DEVICE,
                        compute_type=Config.WHISPER_COMPUTE_TYPE
                    )
                except Exception as e:
                    log.error(f"Whisper init error: {e}")
                    self.whisper_model = None
                    self.stt_engine = "google"

        if self.tts_engine == "piper":
            if Config.PIPER_MODEL_PATH and PIPER_PY_AVAILABLE:
                try:
                    cfg = Config.PIPER_CONFIG_PATH or None
                    self.piper_voice = PiperVoice.load(Config.PIPER_MODEL_PATH, cfg)
                except Exception as e:
                    log.error(f"Piper init error: {e}")
                    self.piper_voice = None
            elif not Config.PIPER_MODEL_PATH:
                log.warning("PIPER_MODEL_PATH not set. Falling back to gTTS.")
                self.tts_engine = "gtts"

    def ngomong(self, teks):
        if not SR_AVAILABLE or not pygame:
            return
        self._tts_queue.put(teks)

    def _tts_worker(self):
        while not self._stop_event.is_set():
            try:
                teks = self._tts_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if teks is None:
                self._tts_queue.task_done()
                break
            try:
                if self.tts_engine == "piper":
                    if not self._tts_piper(teks):
                        self._tts_gtts(teks)
                else:
                    self._tts_gtts(teks)
            except Exception as e:
                log.error(f"TTS Error: {e}")
            finally:
                self._tts_queue.task_done()

    @staticmethod
    def _new_tts_temp_path(suffix: str) -> str:
        temp_dir = os.environ.get("TEMP", "/tmp")
        filename = f"elara_tts_{uuid.uuid4().hex}{suffix}"
        return os.path.join(temp_dir, filename)

    @staticmethod
    def _safe_remove_file(path: str):
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _play_audio_file(self, path: str):
        if not self._ensure_mixer():
            return
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if self._stop_event.is_set():
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
                break
            pygame.time.delay(100)

    def _tts_gtts(self, teks):
        if not GTTS_AVAILABLE:
            log.warning("gTTS not available.")
            return
        path = self._new_tts_temp_path(".mp3")
        try:
            tts = gTTS(text=teks, lang=Config.VOICE_LANGUAGE or "id", slow=False)
            tts.save(path)
            self._play_audio_file(path)
        finally:
            self._safe_remove_file(path)

    def _tts_piper(self, teks) -> bool:
        out_path = self._new_tts_temp_path(".wav")

        if self.piper_voice:
            try:
                audio = self.piper_voice.synthesize(teks)
                if isinstance(audio, (bytes, bytearray)):
                    with open(out_path, "wb") as f:
                        f.write(audio)
                else:
                    import wave
                    sample_rate = getattr(getattr(self.piper_voice, "config", None), "sample_rate", 22050)
                    with wave.open(out_path, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(sample_rate)
                        wf.writeframes(audio.tobytes())
                self._play_audio_file(out_path)
                return True
            except Exception as e:
                log.error(f"Piper python synth error: {e}")
                return False
            finally:
                self._safe_remove_file(out_path)

        if not Config.PIPER_MODEL_PATH:
            return False
        if not shutil.which(Config.PIPER_BIN):
            log.warning("Piper binary not found.")
            return False

        cmd = [Config.PIPER_BIN, "--model", Config.PIPER_MODEL_PATH, "--output_file", out_path]
        if Config.PIPER_CONFIG_PATH:
            cmd.extend(["--config", Config.PIPER_CONFIG_PATH])
        try:
            res = subprocess.run(cmd, input=teks, text=True, capture_output=True)
            if res.returncode != 0:
                log.error(f"Piper CLI error: {res.stderr}")
                return False
            self._play_audio_file(out_path)
            return True
        except Exception as e:
            log.error(f"Piper CLI failed: {e}")
            return False
        finally:
            self._safe_remove_file(out_path)

    def _transcribe(self, audio):
        if self.stt_engine == "whisper" and self.whisper_model:
            try:
                wav_data = audio.get_wav_data()
                temp_dir = os.environ.get("TEMP", "/tmp")
                wav_path = os.path.join(temp_dir, "elara_stt.wav")
                with open(wav_path, "wb") as f:
                    f.write(wav_data)
                segments, _ = self.whisper_model.transcribe(
                    wav_path, language=Config.VOICE_LANGUAGE or "id"
                )
                text = " ".join([seg.text for seg in segments]).strip()
                return text
            except Exception as e:
                log.error(f"Whisper STT error: {e}")

        if self.stt_engine == "google" and self.recognizer:
            try:
                return self.recognizer.recognize_google(audio, language="id-ID")
            except sr.RequestError as e:
                now = time.time()
                if (now - self._last_stt_error_log) > 30:
                    log.error(f"Google STT request error: {e}")
                    self._last_stt_error_log = now
                return ""
            except Exception as e:
                if "interpreter shutdown" in str(e).lower():
                    return ""
                log.error(f"Google STT error: {e}")
                return ""
        return ""

    def _listen_loop(self):
        log.info("Mic Listening...")
        while not self._stop_event.is_set():
            if not self.recognizer:
                return
            try:
                with sr.Microphone() as source:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    while not self._stop_event.is_set():
                        try:
                            audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=6)
                            text = self._transcribe(audio)
                            if not text:
                                continue
                            log.info(f"Voice Heard: {text}")

                            response = async_runner.run_coroutine(
                                self.elara.handle_command(text, user_id="voice_user")
                            )

                            if response:
                                self.ngomong(response)
                        except (sr.WaitTimeoutError, sr.UnknownValueError):
                            continue
                        except RuntimeError as e:
                            if "interpreter shutdown" in str(e).lower():
                                return
                            log.error(f"Voice RuntimeError: {e}")
                            time.sleep(0.5)
                        except Exception as e:
                            if self._stop_event.is_set():
                                return
                            log.error(f"Voice Error: {e}")
                            time.sleep(0.5)
            except RuntimeError as e:
                if "interpreter shutdown" in str(e).lower():
                    return
                if self._stop_event.is_set():
                    return
                log.error(f"Voice runtime init error: {e}")
                time.sleep(1)
            except Exception as e:
                if self._stop_event.is_set():
                    return
                log.error(f"Voice init error: {e}")
                time.sleep(2)

    def _ensure_mixer(self) -> bool:
        if not pygame:
            return False
        with self._mixer_lock:
            if self._mixer_initialized and pygame.mixer.get_init():
                return True
            try:
                pygame.mixer.init()
                self._mixer_initialized = True
                return True
            except Exception as e:
                log.error(f"pygame mixer init failed: {e}")
                self._mixer_initialized = False
                return False

    def _watchdog_loop(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._watchdog_interval_sec):
                break
            try:
                if self._stop_event.is_set():
                    break
                if self._listen_thread and not self._listen_thread.is_alive():
                    log.warning("Voice watchdog restarting STT thread.")
                    self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True, name="VoiceSTT")
                    self._listen_thread.start()
                if self._tts_thread and not self._tts_thread.is_alive():
                    log.warning("Voice watchdog restarting TTS thread.")
                    self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True, name="VoiceTTS")
                    self._tts_thread.start()
            except Exception as e:
                log.warning(f"Voice watchdog error: {e}")

    def start(self):
        if not SR_AVAILABLE or not pygame:
            log.warning("Voice libraries not found.")
            return
        self._stop_event.clear()
        if not self._tts_thread or not self._tts_thread.is_alive():
            self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True, name="VoiceTTS")
            self._tts_thread.start()
        if not self._listen_thread or not self._listen_thread.is_alive():
            self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True, name="VoiceSTT")
            self._listen_thread.start()
        if not self._watchdog_thread or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="VoiceWatchdog")
            self._watchdog_thread.start()

    def stop(self):
        """Gracefully stop STT/TTS workers and release audio resources."""
        self._stop_event.set()
        try:
            self._tts_queue.put_nowait(None)
        except Exception:
            pass

        if self._listen_thread and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=5)
            if self._listen_thread.is_alive():
                log.warning("Voice STT thread did not stop cleanly before timeout.")
        if self._tts_thread and self._tts_thread.is_alive():
            self._tts_thread.join(timeout=5)
            if self._tts_thread.is_alive():
                log.warning("Voice TTS thread did not stop cleanly before timeout.")
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=3)
            if self._watchdog_thread.is_alive():
                log.warning("Voice watchdog thread did not stop cleanly before timeout.")
        self._listen_thread = None
        self._tts_thread = None
        self._watchdog_thread = None

        if pygame:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                    pygame.mixer.quit()
                    self._mixer_initialized = False
            except Exception:
                pass

    def shutdown(self):
        self.stop()

    def close(self):
        self.stop()

    def is_healthy(self):
        if not SR_AVAILABLE or not pygame:
            return True
        if self._stop_event.is_set():
            return True
        if not self._listen_thread or not self._listen_thread.is_alive():
            return False
        if self._tts_thread and not self._tts_thread.is_alive():
            return False
        return True

