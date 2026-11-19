# -*- coding: utf-8 -*-
"""
PC Kontrol Windows Hizmeti (SCM) ve Konsol Çalıştırıcısı.

Saf ctypes ile Win32 Service Control Manager (advapi32) protokolünü uygular:
- SERVICE_START_PENDING -> SERVICE_RUNNING -> SERVICE_STOP_PENDING -> SERVICE_STOPPED
- --console bayrağıyla veya doğrudan konsoldan başlatıldığında (SCM hata kodu 1063)
  ön planda çalışır ve SIGINT / SIGTERM sinyalleriyle temiz şekilde kapanır.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import tempfile
import threading
import time

SERVICE_NAME = "PCKontrolServisi"
DEFAULT_PORT = 8765
LOG_MAX_BYTES = 512 * 1024
LOG_BACKUP_COUNT = 2

# Win32 SCM Sabitleri
SERVICE_WIN32_OWN_PROCESS = 0x00000010

SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004

SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004

SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_INTERROGATE = 0x00000004
SERVICE_CONTROL_SHUTDOWN = 0x00000005

NO_ERROR = 0
ERROR_CALL_NOT_IMPLEMENTED = 120
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063
ERROR_SERVICE_SPECIFIC_ERROR = 1066


def _ensure_stdio_streams() -> None:
    """console=False (PyInstaller/pythonw) modunda None olan stdio akışlarını güvenli hale getirir."""
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            pass
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull, "r", encoding="utf-8")
        except OSError:
            pass


def _configure_sys_path() -> None:
    """Hem frozen (_MEIPASS) hem geliştirme modunda pc_server modüllerini sys.path'e ekler."""
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if meipass:
            candidates.append(meipass)
            candidates.append(os.path.join(meipass, "pc_server"))
        candidates.extend(
            [
                exe_dir,
                os.path.join(exe_dir, "pc_server"),
                os.path.join(exe_dir, "_internal"),
                os.path.join(exe_dir, "_internal", "pc_server"),
            ]
        )
    else:
        service_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.abspath(os.path.join(service_dir, ".."))
        candidates.append(os.path.join(project_dir, "pc_server"))

    for path in reversed(candidates):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _default_log_path() -> str:
    program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return os.path.join(program_data, "PCKontrol", "service.log")


def _fallback_log_path() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(local_app_data, "PCKontrol", "service.log")


class _ServiceLoggingManager:
    """RotatingFileHandler ve isteğe bağlı konsol çıktısını tek yerden yönetir."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._file_handler: RotatingFileHandler | None = None
        self._file_path: str | None = None
        self._console_handler: logging.StreamHandler | None = None

    def configure(self, log_path: str | None = None, console: bool = False) -> logging.Logger:
        with self._lock:
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            target_path = os.path.abspath(log_path or _default_log_path())
            if self._file_path is None or os.path.normcase(self._file_path) != os.path.normcase(target_path):
                new_handler = self._create_file_handler(target_path, formatter)
                if new_handler is not None:
                    if self._file_handler is not None:
                        root_logger.removeHandler(self._file_handler)
                        try:
                            self._file_handler.close()
                        except Exception:
                            pass
                    self._file_handler = new_handler
                    self._file_path = os.path.abspath(new_handler.baseFilename)
                    root_logger.addHandler(new_handler)

            if console and self._console_handler is None and sys.stderr is not None:
                try:
                    stream_handler = logging.StreamHandler(sys.stderr)
                    stream_handler.setLevel(logging.INFO)
                    stream_handler.setFormatter(formatter)
                    self._console_handler = stream_handler
                    root_logger.addHandler(stream_handler)
                except Exception:
                    pass

            return logging.getLogger("PCKontrolService")

    @staticmethod
    def _create_file_handler(target_path: str, formatter: logging.Formatter) -> RotatingFileHandler | None:
        for candidate in (target_path, _fallback_log_path()):
            try:
                os.makedirs(os.path.dirname(candidate), exist_ok=True)
                handler = RotatingFileHandler(
                    candidate,
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                    encoding="utf-8",
                )
                handler.setLevel(logging.INFO)
                handler.setFormatter(formatter)
                return handler
            except OSError:
                continue
        return None


_logging_manager = _ServiceLoggingManager()
LOGGER = logging.getLogger("PCKontrolService")


# ---------------------------------------------------------------------------
# Win32 SCM ctypes Yapıları ve Prototipleri
# ---------------------------------------------------------------------------
class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


SERVICE_MAIN_FUNC = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
HANDLER_EX_FUNC = ctypes.WINFUNCTYPE(
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPVOID,
)


class SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", SERVICE_MAIN_FUNC),
    ]


if sys.platform == "win32":
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(SERVICE_TABLE_ENTRYW)]
    _advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL

    _advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
        wintypes.LPCWSTR,
        HANDLER_EX_FUNC,
        wintypes.LPVOID,
    ]
    _advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE

    _advapi32.SetServiceStatus.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(SERVICE_STATUS),
    ]
    _advapi32.SetServiceStatus.restype = wintypes.BOOL
else:
    _advapi32 = None


def _import_server_module():
    """sys.path ayarını güncelleyip pc_server/server.py modülünü yükler."""
    _configure_sys_path()
    import server  # type: ignore import-not-found

    server_log_path = getattr(server, "LOG_PATH", None)
    if isinstance(server_log_path, str) and server_log_path.strip():
        _logging_manager.configure(log_path=server_log_path)
    return server


def _create_uvicorn_server(server_mod, port: int):
    """Tercihen server.create_uvicorn_server üzerinden Uvicorn Server örneği oluşturur."""
    factory = getattr(server_mod, "create_uvicorn_server", None)
    if callable(factory):
        return factory(host="0.0.0.0", port=port)

    import uvicorn

    config = uvicorn.Config(
        server_mod.app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )
    return uvicorn.Server(config)


def _stop_background_services(server_mod) -> None:
    """Sunucunun arka plan iş parçacıklarını idempotent biçimde durdurur."""
    if server_mod is None:
        return
    stop_bg = getattr(server_mod, "stop_background_services", None)
    if callable(stop_bg):
        try:
            stop_bg()
            return
        except Exception as exc:
            LOGGER.exception("stop_background_services çağrısında hata: %s", exc)

    hw_monitor = getattr(server_mod, "hardware_monitor", None)
    if hw_monitor is not None and hasattr(hw_monitor, "stop"):
        try:
            hw_monitor.stop()
        except Exception as exc:
            LOGGER.exception("hardware_monitor.stop hatası: %s", exc)

    stop_udp = getattr(server_mod, "stop_udp_discovery_server", None)
    if callable(stop_udp):
        try:
            stop_udp()
        except Exception as exc:
            LOGGER.exception("stop_udp_discovery_server hatası: %s", exc)


class WindowsServiceRuntime:
    """
    Win32 SCM yaşam döngüsünü ve ctypes callback referanslarını yöneten sınıf.

    Callback nesneleri (service_main_cb, handler_ex_cb, service_table) örnek ve modül
    kapsamında saklanarak GC tarafından toplanması engellenir.
    """

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._status_handle: wintypes.HANDLE | None = None
        self._status_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._bg_stopped = False
        self._server_mod = None
        self._uvicorn_server = None
        self._current_status = SERVICE_STATUS(
            dwServiceType=SERVICE_WIN32_OWN_PROCESS,
            dwCurrentState=SERVICE_START_PENDING,
            dwControlsAccepted=0,
            dwWin32ExitCode=NO_ERROR,
            dwServiceSpecificExitCode=0,
            dwCheckPoint=0,
            dwWaitHint=0,
        )

        # GC korumalı ctypes callback referansları
        self.service_main_cb = SERVICE_MAIN_FUNC(self._service_main)
        self.handler_ex_cb = HANDLER_EX_FUNC(self._handler_ex)
        self.service_table = (SERVICE_TABLE_ENTRYW * 2)(
            SERVICE_TABLE_ENTRYW(SERVICE_NAME, self.service_main_cb),
            SERVICE_TABLE_ENTRYW(None, SERVICE_MAIN_FUNC()),
        )

    def _report_status(
        self,
        state: int,
        controls_accepted: int = 0,
        win32_exit_code: int = NO_ERROR,
        service_specific_exit_code: int = 0,
        check_point: int = 0,
        wait_hint: int = 0,
    ) -> None:
        if _advapi32 is None or not self._status_handle:
            return
        with self._status_lock:
            self._current_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS
            self._current_status.dwCurrentState = state
            self._current_status.dwControlsAccepted = controls_accepted
            self._current_status.dwWin32ExitCode = win32_exit_code
            self._current_status.dwServiceSpecificExitCode = service_specific_exit_code
            self._current_status.dwCheckPoint = check_point
            self._current_status.dwWaitHint = wait_hint
            ok = _advapi32.SetServiceStatus(self._status_handle, ctypes.byref(self._current_status))
            if not ok:
                err = ctypes.get_last_error()
                LOGGER.warning("SetServiceStatus başarısız (state=%s, err=%s)", state, err)

    def _request_shutdown(self) -> None:
        self._stop_event.set()
        if self._uvicorn_server is not None:
            try:
                self._uvicorn_server.should_exit = True
            except Exception as exc:
                LOGGER.exception("uvicorn_server.should_exit ayarlanamadı: %s", exc)
        with self._shutdown_lock:
            if not self._bg_stopped and self._server_mod is not None:
                self._bg_stopped = True
                _stop_background_services(self._server_mod)

    def _handler_ex(self, dw_control: int, dw_event_type: int, lp_event_data, lp_context) -> int:
        if dw_control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            signal_name = "STOP" if dw_control == SERVICE_CONTROL_STOP else "SHUTDOWN"
            LOGGER.info("SCM kontrol sinyali alındı: %s", signal_name)
            self._report_status(
                SERVICE_STOP_PENDING,
                controls_accepted=0,
                check_point=1,
                wait_hint=10000,
            )
            self._request_shutdown()
            return NO_ERROR

        if dw_control == SERVICE_CONTROL_INTERROGATE:
            with self._status_lock:
                if _advapi32 is not None and self._status_handle:
                    _advapi32.SetServiceStatus(self._status_handle, ctypes.byref(self._current_status))
            return NO_ERROR

        return ERROR_CALL_NOT_IMPLEMENTED

    def _service_main(self, dw_argc: int, lpsz_argv) -> None:
        if _advapi32 is None:
            return

        handle = _advapi32.RegisterServiceCtrlHandlerExW(SERVICE_NAME, self.handler_ex_cb, None)
        if not handle:
            err = ctypes.get_last_error()
            LOGGER.error("RegisterServiceCtrlHandlerExW başarısız: err=%s", err)
            return

        self._status_handle = handle
        self._report_status(
            SERVICE_START_PENDING,
            controls_accepted=0,
            check_point=1,
            wait_hint=15000,
        )
        LOGGER.info("%s başlatılıyor (port=%d)...", SERVICE_NAME, self.port)

        win32_exit = NO_ERROR
        specific_exit = 0
        uvicorn_error: list[BaseException] = []

        try:
            self._server_mod = _import_server_module()
            if self._stop_event.is_set():
                return

            self._uvicorn_server = _create_uvicorn_server(self._server_mod, self.port)

            def _serve() -> None:
                try:
                    self._uvicorn_server.run()
                except BaseException as exc:
                    uvicorn_error.append(exc)

            serve_thread = threading.Thread(target=_serve, name="PCKontrolUvicornThread", daemon=False)
            serve_thread.start()

            checkpoint = 2
            deadline = time.monotonic() + 20.0
            while serve_thread.is_alive() and not self._stop_event.is_set():
                if getattr(self._uvicorn_server, "started", False):
                    break
                if time.monotonic() >= deadline:
                    LOGGER.warning("Uvicorn başlatma bekleme süresi aşıldı; durum RUNNING olarak bildiriliyor.")
                    break
                self._report_status(
                    SERVICE_START_PENDING,
                    controls_accepted=0,
                    check_point=checkpoint,
                    wait_hint=5000,
                )
                checkpoint += 1
                time.sleep(0.4)

            if not self._stop_event.is_set() and serve_thread.is_alive():
                self._report_status(
                    SERVICE_RUNNING,
                    controls_accepted=SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN,
                )
                LOGGER.info("%s çalışıyor (http://0.0.0.0:%d).", SERVICE_NAME, self.port)

            while serve_thread.is_alive():
                serve_thread.join(timeout=1.0)
                if self._stop_event.is_set() and serve_thread.is_alive():
                    self._report_status(
                        SERVICE_STOP_PENDING,
                        controls_accepted=0,
                        check_point=2,
                        wait_hint=5000,
                    )

            if uvicorn_error:
                exc = uvicorn_error[0]
                if isinstance(exc, SystemExit):
                    code = exc.code if isinstance(exc.code, int) else 1
                    if code != 0 and not self._stop_event.is_set():
                        win32_exit = ERROR_SERVICE_SPECIFIC_ERROR
                        specific_exit = code
                        LOGGER.error("Uvicorn SystemExit(%s) ile sonlandı.", code)
                else:
                    win32_exit = ERROR_SERVICE_SPECIFIC_ERROR
                    specific_exit = 1
                    LOGGER.error("Uvicorn beklenmeyen hatayla durdu: %s", exc, exc_info=exc)

        except Exception as exc:
            win32_exit = ERROR_SERVICE_SPECIFIC_ERROR
            specific_exit = 1
            LOGGER.exception("%s çalıştırılırken kritik hata oluştu: %s", SERVICE_NAME, exc)
        finally:
            self._report_status(
                SERVICE_STOP_PENDING,
                controls_accepted=0,
                check_point=3,
                wait_hint=5000,
            )
            self._request_shutdown()
            self._report_status(
                SERVICE_STOPPED,
                controls_accepted=0,
                win32_exit_code=win32_exit,
                service_specific_exit_code=specific_exit,
            )
            LOGGER.info("%s durduruldu (exit=%s/%s).", SERVICE_NAME, win32_exit, specific_exit)

    def start_dispatcher(self) -> int:
        """StartServiceCtrlDispatcherW çağrısını başlatır; 0 başarılı, aksi halde Win32 hata kodu döner."""
        if _advapi32 is None:
            return ERROR_FAILED_SERVICE_CONTROLLER_CONNECT
        ok = _advapi32.StartServiceCtrlDispatcherW(self.service_table)
        if not ok:
            return ctypes.get_last_error()
        return NO_ERROR


# Modül kapsamında aktif runtime referansı (GC koruması için)
_ACTIVE_RUNTIME: WindowsServiceRuntime | None = None


def run_console(port: int = DEFAULT_PORT) -> int:
    """Ön planda (konsol) sunucuyu başlatır; SIGINT/SIGTERM ile temiz kapanır."""
    _logging_manager.configure(console=True)
    LOGGER.info("Konsol modunda başlatılıyor (http://0.0.0.0:%d)...", port)

    server_mod = _import_server_module()
    uvicorn_server = _create_uvicorn_server(server_mod, port)
    stop_once = threading.Lock()
    bg_stopped = False

    def _cleanup() -> None:
        nonlocal bg_stopped
        with stop_once:
            if not bg_stopped:
                bg_stopped = True
                _stop_background_services(server_mod)

    def _handle_signal(signum, _frame) -> None:
        LOGGER.info("Kapanma sinyali alındı (%s); sunucu durduruluyor...", signum)
        uvicorn_server.should_exit = True

    previous_handlers: dict[int, object] = {}
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                previous_handlers[sig] = signal.signal(sig, _handle_signal)
            except Exception:
                pass

    exit_code = 0
    try:
        uvicorn_server.run()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        exit_code = int(code or 0)
    except KeyboardInterrupt:
        uvicorn_server.should_exit = True
    except Exception as exc:
        LOGGER.exception("Konsol modunda sunucu hatası: %s", exc)
        exit_code = 1
    finally:
        for sig, prev in previous_handlers.items():
            try:
                signal.signal(sig, prev)
            except Exception:
                pass
        _cleanup()
        LOGGER.info("Konsol sunucusu temiz şekilde kapandı (kod=%d).", exit_code)

    return exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PCKontrolService",
        description="PC Kontrol Windows Hizmeti ve Konsol Sunucu Başlatıcısı",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Windows Hizmeti yerine doğrudan ön planda (konsol modunda) çalıştır.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"HTTP/API dinleme portu (varsayılan: {DEFAULT_PORT}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    global _ACTIVE_RUNTIME

    _ensure_stdio_streams()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not (1 <= args.port <= 65535):
        parser.error(f"--port 1 ile 65535 arasında olmalıdır (verilen: {args.port}).")

    _logging_manager.configure(console=bool(args.console))

    if args.console:
        return run_console(port=args.port)

    runtime = WindowsServiceRuntime(port=args.port)
    _ACTIVE_RUNTIME = runtime
    err = runtime.start_dispatcher()
    if err == NO_ERROR:
        return 0

    if err == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
        LOGGER.info(
            "StartServiceCtrlDispatcherW hata kodu 1063 döndü (SCM bağlantısı yok); "
            "konsol moduna geçiliyor."
        )
        return run_console(port=args.port)

    LOGGER.error("StartServiceCtrlDispatcherW başarısız oldu: Win32 hata kodu %d", err)
    return 1


if __name__ == "__main__":
    sys.exit(main())
