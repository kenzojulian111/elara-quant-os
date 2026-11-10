import asyncio
import threading
import logging
import base64
import io
import ssl
import secrets
import cv2
from urllib.parse import urlparse, parse_qs

from config.settings import Config

try:
    import websockets
    import mss
    from PIL import Image
    STREAM_AVAILABLE = True
except ImportError:
    STREAM_AVAILABLE = False

log = logging.getLogger("elara.stream")

def _extract_cookie_token(cookie_header: str) -> str:
    raw = str(cookie_header or "")
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if str(key).strip() == "stream_auth":
            return str(value or "").strip()
    return ""


def _is_authorized(path: str, headers=None) -> bool:
    token = Config.STREAM_TOKEN
    if not token:
        return False
    expected = str(token or "").strip()
    if not expected:
        return False
    provided = ""
    if headers:
        try:
            auth_header = str(headers.get("Authorization") or "").strip()
            if auth_header.lower().startswith("bearer "):
                provided = auth_header.split(" ", 1)[1].strip()
        except Exception:
            provided = ""
        if not provided:
            try:
                provided = _extract_cookie_token(headers.get("Cookie") or "")
            except Exception:
                provided = ""
    if not provided:
        allow_query = bool(getattr(Config, "STREAM_ALLOW_QUERY_TOKEN", False))
        if not allow_query:
            return False
        try:
            provided = parse_qs(urlparse(path).query).get("token", [None])[0]
        except Exception:
            return False
    try:
        return bool(provided) and secrets.compare_digest(str(provided), expected)
    except Exception:
        return False

class StreamService:
    """Background WebSocket streaming service for desktop and CCTV feeds."""

    def __init__(self):
        self._thread = None
        self._watchdog_thread = None
        self._loop = None
        self._servers = []
        self._stop_event = threading.Event()
        self._stream_quality = max(15, min(90, int(getattr(Config, "STREAM_JPEG_QUALITY", 55))))
        self._stream_quality_min = max(10, min(80, int(getattr(Config, "STREAM_JPEG_QUALITY_MIN", 20))))
        self._stream_quality_max = max(self._stream_quality_min, min(95, int(getattr(Config, "STREAM_JPEG_QUALITY_MAX", 70))))
        self._quality_step = max(1, int(getattr(Config, "STREAM_JPEG_QUALITY_STEP", 5)))
        self._watchdog_interval_sec = max(2, int(getattr(Config, "STREAM_WATCHDOG_INTERVAL_SEC", 6)))
        self._lock = threading.Lock()
        self._ssl_context = None

    def _adjust_quality(self, success: bool):
        with self._lock:
            if success:
                self._stream_quality = min(self._stream_quality_max, self._stream_quality + 1)
            else:
                self._stream_quality = max(self._stream_quality_min, self._stream_quality - self._quality_step)

    def _get_quality(self) -> int:
        with self._lock:
            return int(self._stream_quality)

    @staticmethod
    def _cctv_read_frame(cap):
        if cap is None:
            return False, None
        return cap.read()

    @staticmethod
    def _encode_cctv_frame(frame, quality: int):
        ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            return None
        return base64.b64encode(buffer).decode('utf-8')

    @staticmethod
    def _encode_screen_frame(sct_img, quality: int):
        img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=int(quality))
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _watchdog_loop(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._watchdog_interval_sec):
                break
            try:
                if self._stop_event.is_set():
                    break
                if self._thread and not self._thread.is_alive():
                    log.warning("Stream watchdog restarting websocket server thread.")
                    self.start()
            except Exception as e:
                log.warning(f"Stream watchdog error: {e}")

    def start(self):
        """Start stream servers on dedicated loop/thread if dependencies are available."""
        if not STREAM_AVAILABLE:
            log.warning("Stream libs (websockets/mss) not found.")
            return

        if not Config.STREAM_TOKEN:
            log.warning("STREAM_TOKEN kosong. Stream dinonaktifkan.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        tls_cert = str(getattr(Config, "STREAM_TLS_CERT_PATH", "") or "").strip()
        tls_key = str(getattr(Config, "STREAM_TLS_KEY_PATH", "") or "").strip()
        require_tls = bool(getattr(Config, "STREAM_REQUIRE_TLS", False))
        self._ssl_context = None
        if tls_cert and tls_key:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(certfile=tls_cert, keyfile=tls_key)
                self._ssl_context = ctx
                log.info("Stream TLS context loaded. Serving over WSS.")
            except Exception as e:
                log.error(f"Failed loading stream TLS cert/key: {e}")
        if require_tls and not self._ssl_context:
            log.error("STREAM_REQUIRE_TLS=1 but TLS cert/key unavailable. Stream disabled.")
            return

        # Handler Remote Desktop
        async def remote_desktop(websocket, path):
            if not _is_authorized(path, headers=getattr(websocket, "request_headers", None)):
                await websocket.close(code=1008, reason="Unauthorized")
                return
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                try:
                    while not self._stop_event.is_set():
                        sct_img = sct.grab(monitor)
                        img_str = await asyncio.to_thread(
                            self._encode_screen_frame, sct_img, self._get_quality()
                        )
                        try:
                            await websocket.send(img_str)
                            self._adjust_quality(success=True)
                            await asyncio.sleep(0.1)
                        except Exception:
                            self._adjust_quality(success=False)
                            raise
                except Exception as e:
                    log.warning(f"Remote desktop stream closed: {e}")

        # Handler CCTV
        async def cctv(websocket, path):
            if not _is_authorized(path, headers=getattr(websocket, "request_headers", None)):
                await websocket.close(code=1008, reason="Unauthorized")
                return
            cap = cv2.VideoCapture(0)
            try:
                while cap.isOpened() and not self._stop_event.is_set():
                    ret, frame = await asyncio.to_thread(self._cctv_read_frame, cap)
                    if not ret:
                        break
                    img_str = await asyncio.to_thread(self._encode_cctv_frame, frame, self._get_quality())
                    if not img_str:
                        self._adjust_quality(success=False)
                        await asyncio.sleep(0.05)
                        continue
                    try:
                        await websocket.send(img_str)
                        self._adjust_quality(success=True)
                        await asyncio.sleep(0.1)
                    except Exception:
                        self._adjust_quality(success=False)
                        raise
            except Exception as e:
                log.warning(f"CCTV stream closed: {e}")
            finally:
                cap.release()

        # Jalankan WebSocket Server di Event Loop Baru
        def run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                srv1 = self._loop.run_until_complete(
                    websockets.serve(remote_desktop, Config.STREAM_BIND_HOST, 8765, ssl=self._ssl_context)
                )
                srv2 = self._loop.run_until_complete(
                    websockets.serve(cctv, Config.STREAM_BIND_HOST, 8766, ssl=self._ssl_context)
                )
                self._servers = [srv1, srv2]
                log.info("WebSocket Streams Started on :8765 & :8766")
                self._loop.run_forever()
            finally:
                for srv in self._servers:
                    try:
                        srv.close()
                        self._loop.run_until_complete(srv.wait_closed())
                    except Exception:
                        pass
                self._servers = []
                try:
                    self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                except Exception:
                    pass
                self._loop.close()
                self._loop = None

        self._thread = threading.Thread(target=run_server, daemon=True, name="ElaraStream")
        self._thread.start()
        if not self._watchdog_thread or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="ElaraStreamWatchdog")
            self._watchdog_thread.start()

    def stop(self):
        """Gracefully stop stream servers and join worker thread."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            async def _close_servers():
                servers = list(self._servers)
                for srv in servers:
                    try:
                        srv.close()
                    except Exception:
                        pass
                if servers:
                    await asyncio.gather(*[srv.wait_closed() for srv in servers], return_exceptions=True)
                # Cancel pending tasks on this loop to avoid dangling transports.
                current = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            try:
                fut = asyncio.run_coroutine_threadsafe(_close_servers(), self._loop)
                fut.result(timeout=5)
            except Exception as e:
                log.warning(f"Stream async close failed: {e}")
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception as e:
                log.warning(f"Stream loop stop failed: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                log.warning("Stream thread did not stop cleanly before timeout.")
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=3)
            if self._watchdog_thread.is_alive():
                log.warning("Stream watchdog thread did not stop cleanly before timeout.")
        self._thread = None
        self._watchdog_thread = None
        self._servers = []
        self._ssl_context = None

    def shutdown(self):
        self.stop()

    def close(self):
        self.stop()

    def is_healthy(self):
        if not STREAM_AVAILABLE or not Config.STREAM_TOKEN:
            return True
        return self._thread is not None and self._thread.is_alive()

