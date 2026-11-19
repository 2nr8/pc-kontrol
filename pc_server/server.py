# -*- coding: utf-8 -*-
"""
PC Güç ve Durum Kontrol Arka Plan Servisi
Saf ASGI Uygulaması (Uvicorn ile doğrudan çalışır)
Port: 8765
"""

import os
import sys

# pythonw.exe altında sys.stdout/stderr None olduğu için Uvicorn'un çökmesini önle
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stdin is None:
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
    except Exception:
        pass

import asyncio
import ctypes
from ctypes import wintypes
import json
import time
import socket
import re
import mimetypes
import subprocess
import threading
import atexit
import getpass
import tempfile
from urllib.parse import urlparse, parse_qs
import psutil

# -------------------------------------------------------------
# Sabitler ve Yapılandırma
# -------------------------------------------------------------
DEFAULT_MAC = ""
DEFAULT_IP = ""
DEFAULT_BROADCAST = "255.255.255.255"
SERVER_PORT = 8765
UDP_DISCOVERY_PORT = 8765

IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    APP_ROOT = os.path.dirname(os.path.abspath(sys.executable))
    _MEIPASS_DIR = getattr(sys, "_MEIPASS", APP_ROOT)
    BASE_DIR = APP_ROOT
    PROJECT_DIR = APP_ROOT
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
    APP_ROOT = BASE_DIR
    _MEIPASS_DIR = BASE_DIR


def _resolve_resource_dir(name: str) -> str:
    """Kaynak dizinini (static, lib, dist) PyInstaller frozen veya geliştirme moduna göre çözümler."""
    if IS_FROZEN:
        app_candidate = os.path.join(APP_ROOT, name)
        if os.path.isdir(app_candidate):
            return app_candidate
        meipass_candidate = os.path.join(_MEIPASS_DIR, name)
        if os.path.isdir(meipass_candidate):
            return meipass_candidate
        return app_candidate
    parent = PROJECT_DIR if name == "dist" else BASE_DIR
    return os.path.join(parent, name)


def _resolve_data_dir() -> str:
    """Kalıcı veri ve log dizinini çözümler (Frozen modda %PROGRAMDATA%\\PCKontrol)."""
    if IS_FROZEN:
        program_data = (os.environ.get("PROGRAMDATA") or r"C:\ProgramData").strip() or r"C:\ProgramData"
        data_dir = os.path.join(program_data, "PCKontrol")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception:
            pass
        return data_dir
    return BASE_DIR


STATIC_DIR = _resolve_resource_dir("static")
LIB_DIR = _resolve_resource_dir("lib")
LHM_DLL_PATH = os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll")
DIST_DIR = _resolve_resource_dir("dist")
APK_PATH = os.path.join(DIST_DIR, "PC.apk")
LEGACY_APK_PATH = os.path.join(DIST_DIR, "PC-Kontrol.apk")
DATA_DIR = _resolve_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "service.log")

_config_lock = threading.Lock()
_GENERIC_HOST_RE = re.compile(r"^(?:DESKTOP|LAPTOP|WIN)-[A-Z0-9]{5,}$", re.IGNORECASE)
_SYSTEM_USER_EXCLUDE = {
    "system",
    "local service",
    "network service",
    "administrator",
    "defaultaccount",
    "wdagutilityaccount",
    "guest",
}
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Windows pencere gizleme bayrağı
CREATE_NO_WINDOW = 0x08000000

# Bellek içi durum değişkenleri
scheduled_shutdown_target = None  # epoch timestamp (float)
_udp_discovery_started = False
_udp_running = False
_udp_socket = None

# psutil ilk başlatma (CPU yüzdesinin ilk okumada 0 kalmaması için)
psutil.cpu_percent(interval=None)


# -------------------------------------------------------------
# Donanım Sensör İzleyici (LibreHardwareMonitor & nvidia-smi Fallback)
# -------------------------------------------------------------
def _safe_float(val, min_val: float = None, max_val: float = None):
    """Değeri güvenli şekilde float'a çevirir; geçersiz, [N/A], NaN, inf veya sınır dışı ise None döner."""
    import math

    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
    else:
        s = str(val).strip()
        if not s or s.startswith("["):
            return None
        try:
            f = float(s)
        except (ValueError, TypeError):
            return None

    if math.isnan(f) or math.isinf(f):
        return None
    if min_val is not None and f < min_val:
        return None
    if max_val is not None and f > max_val:
        return None
    return f


def _safe_int(val, min_val: int = None, max_val: int = None):
    """Değeri güvenli şekilde int'e çevirir; geçersiz veya sınır dışı ise None döner."""
    f = _safe_float(
        val,
        min_val=float(min_val) if min_val is not None else None,
        max_val=float(max_val) if max_val is not None else None,
    )
    if f is None:
        return None
    try:
        return int(round(f))
    except (ValueError, OverflowError):
        return None


def _score_cpu_sensor(name: str) -> int:
    """CPU sıcaklık sensörlerini güvenilirlik ve önem sırasına göre puanlar (Düşük puan = Yüksek öncelik)."""
    n = name.lower().strip()
    # AMD Ryzen Tctl/Tdie ve Intel CPU Package en yüksek önceliklidir
    if "tctl" in n and "tdie" in n:
        return 1
    if "cpu package" in n:
        return 2
    if "tdie" in n:
        return 3
    if "tctl" in n:
        return 4
    if "package" in n:
        return 5
    if "ccd" in n:  # Core CCD #1, CCD1 vb.
        return 6
    if "core average" in n or "average" in n:
        return 7
    if "core max" in n:
        return 8
    if "cpu core" in n:
        return 9
    if "cpu total" in n or "cpu" in n:
        return 10
    if "core #" in n:
        return 11
    return 20


def _update_subhardware(hardware, depth: int = 0, max_depth: int = 8):
    """Alt donanımları özyinelemeli olarak günceller (derinlik sınırlı)."""
    if depth >= max_depth:
        return
    try:
        for sub in hardware.SubHardware:
            try:
                sub.Update()
            except Exception:
                pass
            _update_subhardware(sub, depth=depth + 1, max_depth=max_depth)
    except Exception:
        pass


def _get_all_sensors(hardware, depth: int = 0, max_depth: int = 8):
    """Verilen donanım ve tüm alt donanımlardaki sensörleri özyinelemeli olarak toplar."""
    if depth > max_depth:
        return []
    sensors = []
    try:
        for s in hardware.Sensors:
            sensors.append(s)
    except Exception:
        pass
    try:
        for sub in hardware.SubHardware:
            sensors.extend(_get_all_sensors(sub, depth=depth + 1, max_depth=max_depth))
    except Exception:
        pass
    return sensors


class HardwareMonitor:
    """
    LibreHardwareMonitorLib.dll (.NET / pythonnet) ve nvidia-smi fallback desteği ile
    CPU ve GPU donanım sensörlerini (sıcaklık, kullanım, VRAM) arka planda periyodik
    ve thread-safe olarak okuyan ve önbelleğe alan izleyici sınıfı.
    """
    def __init__(self, interval: float = 1.8):
        self._interval = max(0.2, float(interval))
        self._lock = threading.Lock()
        self._lhm_lock = threading.RLock()
        self._sample_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cache = {
            "cpu_temp_c": None,
            "gpu": {
                "available": False,
                "name": None,
                "temp_c": None,
                "util_percent": None,
                "mem_used_mb": None,
                "mem_total_mb": None,
            },
        }
        self._running = False
        self._thread = None
        self._computer = None
        self._lhm_loaded = False
        self._lhm_ever_loaded = False
        self._lhm_computer_cls = None
        self._empty_lhm_samples = 0
        self._last_lhm_reinit_ts = None
        self._lhm_reinit_cooldown = 30.0
        self._lhm_reinit_attempts = 0
        self._nvidia_smi_available = True
        self._nvidia_smi_path = None
        self._init_lhm()

    def _init_lhm(self):
        """LibreHardwareMonitorLib.dll kütüphanesini clr ile bağlar ve Computer nesnesini açar."""
        with self._lhm_lock:
            if self._stop_event.is_set():
                return
            computer = None
            try:
                if not os.path.isfile(LHM_DLL_PATH):
                    return

                if self._lhm_computer_cls is None:
                    if LIB_DIR not in sys.path:
                        sys.path.append(LIB_DIR)
                    path_entries = os.environ.get("PATH", "").split(os.pathsep)
                    if LIB_DIR not in path_entries:
                        os.environ["PATH"] = LIB_DIR + os.pathsep + os.environ.get("PATH", "")

                    import clr
                    clr.AddReference(os.path.abspath(LHM_DLL_PATH))
                    from LibreHardwareMonitor.Hardware import Computer
                    self._lhm_computer_cls = Computer

                computer = self._lhm_computer_cls()
                computer.IsCpuEnabled = True
                computer.IsGpuEnabled = True
                computer.IsMotherboardEnabled = True
                computer.IsControllerEnabled = True
                computer.Open()

                if self._stop_event.is_set():
                    try:
                        computer.Close()
                    except Exception:
                        pass
                    self._computer = None
                    self._lhm_loaded = False
                    return

                self._computer = computer
                self._lhm_loaded = True
                self._lhm_ever_loaded = True
            except Exception:
                if computer is not None:
                    try:
                        computer.Close()
                    except Exception:
                        pass
                if self._computer is not None and self._computer is not computer:
                    try:
                        self._computer.Close()
                    except Exception:
                        pass
                self._computer = None
                self._lhm_loaded = False

    def _reinit_lhm(self):
        """Uyku/uyanma sonrası takılan veya boş dönen LHM sensör köprüsünü güvenli şekilde yeniden başlatır."""
        if self._stop_event.is_set():
            return False
        with self._lhm_lock:
            if self._stop_event.is_set():
                return False
            if self._computer is not None:
                try:
                    self._computer.Close()
                except Exception:
                    pass
                self._computer = None
            self._lhm_loaded = False
            self._init_lhm()
            return self._lhm_loaded

    def _find_nvidia_smi_candidates(self):
        """PATH ve bilinen Windows konumlarından olası nvidia-smi yollarını döndürür."""
        import shutil

        candidates = []
        seen = set()

        def _add(path):
            norm = os.path.normcase(os.path.abspath(path))
            if norm not in seen:
                seen.add(norm)
                candidates.append(path)

        which_path = shutil.which("nvidia-smi")
        if which_path:
            _add(which_path)

        for known_path in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if os.path.isfile(known_path):
                _add(known_path)

        return candidates

    def _query_nvidia_smi(self):
        """nvidia-smi CLI üzerinden GPU bilgilerini çeken yedek fallback mekanizması."""
        if not self._nvidia_smi_available:
            return None

        query_args = [
            "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name",
            "--format=csv,noheader,nounits",
        ]
        tried = set()
        candidates = [self._nvidia_smi_path] if self._nvidia_smi_path else self._find_nvidia_smi_candidates()

        if not candidates:
            self._nvidia_smi_available = False
            return None

        idx = 0
        rescanned = self._nvidia_smi_path is None
        while idx < len(candidates):
            exe_path = candidates[idx]
            idx += 1
            norm_exe = os.path.normcase(os.path.abspath(exe_path))
            if norm_exe in tried:
                continue
            tried.add(norm_exe)

            try:
                res = subprocess.run(
                    [exe_path] + query_args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1.8,
                    creationflags=CREATE_NO_WINDOW,
                )
                self._nvidia_smi_path = exe_path
                self._nvidia_smi_available = True
                if res.returncode == 0 and res.stdout and res.stdout.strip():
                    lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
                    if lines:
                        parts = [p.strip() for p in lines[0].split(",")]
                        if len(parts) >= 5:
                            temp = _safe_float(parts[0], min_val=0.0, max_val=130.0)
                            util = _safe_float(parts[1], min_val=0.0, max_val=100.0)
                            used = _safe_int(parts[2], min_val=0)
                            total = _safe_int(parts[3], min_val=0)
                            name = ", ".join(parts[4:]).strip() or None
                            if (
                                temp is None
                                and util is None
                                and used is None
                                and total is None
                                and name is None
                            ):
                                return None
                            return {
                                "available": True,
                                "name": name,
                                "temp_c": round(temp, 1) if temp is not None else None,
                                "util_percent": round(util, 1) if util is not None else None,
                                "mem_used_mb": used,
                                "mem_total_mb": total,
                            }
                return None
            except FileNotFoundError:
                if self._nvidia_smi_path == exe_path:
                    self._nvidia_smi_path = None
                if not rescanned:
                    rescanned = True
                    for extra in self._find_nvidia_smi_candidates():
                        if extra not in candidates:
                            candidates.append(extra)
                continue
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, Exception):
                self._nvidia_smi_path = exe_path
                return None

        self._nvidia_smi_path = None
        self._nvidia_smi_available = False
        return None

    def _sample(self):
        """Donanım sensörlerinden CPU ve GPU verilerini okur ve önbelleği günceller."""
        import time

        with self._sample_lock:
            if self._stop_event.is_set():
                return

            cpu_temp = None
            gpu_data = None
            cpu_candidates = []
            mb_candidates = []

            # 1. Aşama: LibreHardwareMonitor ile tarama
            with self._lhm_lock:
                comp = self._computer
                lhm_active = self._lhm_loaded and comp is not None
                if lhm_active:
                    try:
                        for hw in comp.Hardware:
                            if self._stop_event.is_set():
                                break
                            try:
                                hw.Update()
                            except Exception:
                                continue

                            # Alt donanımları da güncelle
                            _update_subhardware(hw)

                            try:
                                hw_type_str = str(hw.HardwareType).lower()
                            except Exception:
                                continue
                            all_sensors = _get_all_sensors(hw)

                            # CPU Sensörleri (AMD Ryzen, Intel Core)
                            if "cpu" in hw_type_str:
                                for s in all_sensors:
                                    try:
                                        stype = str(s.SensorType).lower()
                                        sname = str(s.Name).lower()
                                        raw_val = s.Value
                                    except Exception:
                                        continue
                                    if stype == "temperature":
                                        # Distance to TjMax, Margin gibi sahte/kalan değerleri filtrele
                                        if any(x in sname for x in ("distance", "tjmax", "margin")):
                                            continue
                                        val = _safe_float(raw_val, min_val=5.0, max_val=125.0)
                                        if val is not None:
                                            cpu_candidates.append((_score_cpu_sensor(sname), sname, val))

                            # Anakart & SuperIO & Controller sensörleri üzerinden fallback CPU sıcaklığı
                            elif any(t in hw_type_str for t in ("motherboard", "superio", "controller", "embeddedcontroller")):
                                for s in all_sensors:
                                    try:
                                        stype = str(s.SensorType).lower()
                                        sname = str(s.Name).lower()
                                        raw_val = s.Value
                                    except Exception:
                                        continue
                                    if stype == "temperature":
                                        if any(x in sname for x in ("distance", "tjmax", "margin")):
                                            continue
                                        val = _safe_float(raw_val, min_val=15.0, max_val=115.0)
                                        if val is not None:
                                            # Doğrudan CPU / Socket / Diode etiketi olan anakart sensörleri
                                            if any(k in sname for k in ("cpu", "socket", "diode")):
                                                mb_candidates.append((1, sname, val))
                                            # Standart TMPIN girişleri (TMPIN0, TMPIN1)
                                            elif any(k in sname for k in ("tmpin0", "tmpin1")):
                                                mb_candidates.append((2, sname, val))

                            # GPU Sensörleri (NVIDIA, AMD, Intel GPU)
                            elif "gpu" in hw_type_str and gpu_data is None:
                                try:
                                    g_name = str(hw.Name) if hw.Name else "GPU"
                                except Exception:
                                    g_name = "GPU"
                                g_temp = None
                                g_util = None
                                g_used = None
                                g_total = None

                                for s in all_sensors:
                                    try:
                                        stype = str(s.SensorType).lower()
                                        sname = str(s.Name).lower()
                                        val = _safe_float(s.Value)
                                    except Exception:
                                        continue
                                    if val is None:
                                        continue

                                    # GPU Sıcaklığı
                                    if stype == "temperature":
                                        if any(x in sname for x in ("distance", "tjmax", "margin")):
                                            continue
                                        if 5.0 <= val <= 125.0:
                                            if "core" in sname:
                                                g_temp = round(val, 1)
                                            elif g_temp is None and "hot spot" not in sname:
                                                g_temp = round(val, 1)
                                            elif g_temp is None:
                                                g_temp = round(val, 1)

                                    # GPU Kullanım Yüzdesi
                                    elif stype == "load":
                                        if 0.0 <= val <= 100.0:
                                            if "gpu core" in sname or sname == "core" or (
                                                "core" in sname and not any(x in sname for x in ("memory", "voltage", "clock", "bus"))
                                            ):
                                                g_util = round(val, 1)
                                            elif g_util is None and (
                                                "d3d 3d" in sname or sname == "gpu" or "gpu load" in sname
                                            ):
                                                g_util = round(val, 1)

                                    # VRAM Bellek (MB ve GB birim dönüşümü)
                                    elif stype in ("smalldata", "data"):
                                        val_mb_int = _safe_int(
                                            val if stype == "smalldata" else val * 1024.0,
                                            min_val=0,
                                        )
                                        if val_mb_int is not None:
                                            if "gpu memory used" in sname:
                                                g_used = val_mb_int
                                            elif g_used is None and (
                                                "memory used" in sname or "dedicated memory used" in sname
                                            ):
                                                g_used = val_mb_int
                                            if "gpu memory total" in sname:
                                                g_total = val_mb_int
                                            elif g_total is None and "memory total" in sname and "shared" not in sname:
                                                g_total = val_mb_int

                                if g_temp is not None or g_util is not None or (g_used is not None and g_total is not None):
                                    gpu_data = {
                                        "available": True,
                                        "name": g_name,
                                        "temp_c": g_temp,
                                        "util_percent": g_util,
                                        "mem_used_mb": g_used,
                                        "mem_total_mb": g_total,
                                    }
                    except Exception:
                        pass

            # CPU Sıcaklık Kararı: Doğrudan CPU sensörleri öncelikli, yoksa anakart fallback
            if cpu_candidates:
                cpu_candidates.sort(key=lambda x: x[0])
                cpu_temp = round(cpu_candidates[0][2], 1)
            elif mb_candidates:
                mb_candidates.sort(key=lambda x: x[0])
                cpu_temp = round(mb_candidates[0][2], 1)

            # LHM Sleep/Resume Self-Healing Takibi (nvidia-smi fallback öncesi değerlendirilir)
            should_reinit_lhm = False
            lhm_has_data = (cpu_temp is not None) or (gpu_data is not None)
            if lhm_has_data:
                self._empty_lhm_samples = 0
                self._last_lhm_reinit_ts = None
                self._lhm_reinit_attempts = 0
            elif (self._lhm_loaded or self._lhm_ever_loaded) and not self._stop_event.is_set():
                self._empty_lhm_samples += 1
                if self._empty_lhm_samples >= 5:
                    if self._lhm_loaded or os.path.isfile(LHM_DLL_PATH):
                        now = time.monotonic()
                        cooldown = min(
                            300.0,
                            self._lhm_reinit_cooldown * (2 ** min(self._lhm_reinit_attempts, 3)),
                        )
                        if (
                            self._last_lhm_reinit_ts is None
                            or (now - self._last_lhm_reinit_ts) >= cooldown
                        ):
                            self._empty_lhm_samples = 0
                            self._last_lhm_reinit_ts = now
                            self._lhm_reinit_attempts += 1
                            should_reinit_lhm = True

            # 2. Aşama: GPU okunamadıysa veya eksik metrikler varsa nvidia-smi Fallback
            if (
                not gpu_data
                or not gpu_data.get("available")
                or gpu_data.get("temp_c") is None
                or gpu_data.get("util_percent") is None
                or gpu_data.get("mem_used_mb") is None
            ):
                smi_gpu = self._query_nvidia_smi()
                if smi_gpu:
                    if not gpu_data or not gpu_data.get("available"):
                        gpu_data = smi_gpu
                    else:
                        # Eksik alanları nvidia-smi ile tamamla (veri zenginleştirme)
                        for key in ("temp_c", "util_percent", "mem_used_mb", "mem_total_mb", "name"):
                            if gpu_data.get(key) is None and smi_gpu.get(key) is not None:
                                gpu_data[key] = smi_gpu[key]

            # 3. Aşama: GPU hiçbir şekilde bulunamadıysa varsayılan nesne
            if not gpu_data:
                gpu_data = {
                    "available": False,
                    "name": None,
                    "temp_c": None,
                    "util_percent": None,
                    "mem_used_mb": None,
                    "mem_total_mb": None,
                }

            # 4. Aşama: Thread-safe önbellek güncellemesi (Fail-safe: CPU geçerli aralıkta değilse None döner)
            if not self._stop_event.is_set():
                with self._lock:
                    if not self._stop_event.is_set():
                        self._cache = {
                            "cpu_temp_c": cpu_temp if (cpu_temp is not None and 5.0 <= cpu_temp <= 125.0) else None,
                            "gpu": gpu_data,
                        }

            # Ardışık 5 boş LHM örnekleminde sensör köprüsünü yeniden başlat
            if should_reinit_lhm and not self._stop_event.is_set():
                self._reinit_lhm()

    def _worker(self):
        while not self._stop_event.is_set():
            with self._lock:
                if not self._running:
                    break
            try:
                self._sample()
            except Exception:
                pass
            except BaseException:
                break
            if self._stop_event.wait(self._interval):
                break

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._empty_lhm_samples = 0
            self._last_lhm_reinit_ts = None
            self._lhm_reinit_attempts = 0

        if not self._lhm_loaded and self._lhm_ever_loaded and os.path.isfile(LHM_DLL_PATH):
            self._init_lhm()

        # İlk HTTP isteğinde boş veri dönmemesi için ilk örneklemeyi al
        try:
            self._sample()
        except Exception:
            pass

        with self._lock:
            if not self._running or self._stop_event.is_set():
                return
            self._thread = threading.Thread(target=self._worker, daemon=True, name="HardwareMonitorThread")
            self._thread.start()

    def stop(self):
        with self._lock:
            if not self._running and self._thread is None and self._computer is None:
                return
            self._running = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None

        if thread and thread.is_alive() and thread != threading.current_thread():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

        if self._lhm_lock.acquire(timeout=2.0):
            try:
                if self._computer is not None:
                    try:
                        self._computer.Close()
                    except Exception:
                        pass
                    self._computer = None
                self._lhm_loaded = False
            finally:
                self._lhm_lock.release()

    def get_data(self):
        with self._lock:
            return {
                "cpu_temp_c": self._cache["cpu_temp_c"],
                "gpu": dict(self._cache["gpu"]),
            }


# Donanım İzleyiciyi Başlat (Arka plan periyodu: ~1.8 sn)
hardware_monitor = HardwareMonitor(interval=1.8)
hardware_monitor.start()
atexit.register(hardware_monitor.stop)


# -------------------------------------------------------------
# Yardımcı Fonksiyonlar & Ağ Tespiti
# -------------------------------------------------------------
import ipaddress

_VIRTUAL_IFACE_KEYWORDS = (
    "vethernet",
    "wsl",
    "virtualbox",
    "vmware",
    "vbox",
    "hyper-v",
    "docker",
    "tailscale",
    "zerotier",
    "wireguard",
    "tap",
    "tun",
    "vpn",
    "hamachi",
    "radmin",
    "loopback",
    "bluetooth",
    "npcap",
    "teredo",
    "isatap",
)

_PRIVATE_LAN_NETWORKS = (
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
)


def _parse_usable_ipv4(ip_str):
    """Geçerli ve kullanılabilir (loopback/APIPA/unspecified/multicast olmayan) IPv4Address döndürür."""
    if not ip_str:
        return None
    try:
        addr = ipaddress.IPv4Address(str(ip_str).strip())
    except Exception:
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return None
    return addr


def _is_filtered_ip(ip_str: str) -> bool:
    """Loopback, APIPA, VirtualBox (192.168.56.x) ve Tailscale/CGNAT (100.x.x.x) IP'lerini filtreler."""
    if not _parse_usable_ipv4(ip_str):
        return True
    s = str(ip_str).strip()
    return s.startswith("192.168.56.") or s.startswith("100.")


def _is_private_lan_ip(ip_str: str) -> bool:
    """Özel LAN IP bloklarını (192.168.x.x, 10.x.x.x, 172.16-31.x.x — 192.168.56. hariç) doğrular."""
    if _is_filtered_ip(ip_str):
        return False
    addr = _parse_usable_ipv4(ip_str)
    return bool(addr and any(addr in net for net in _PRIVATE_LAN_NETWORKS))


def _private_block_rank(ip_str: str) -> int:
    """Özel LAN IP blokları için öncelik puanı döndürür (192.168. > 10. > 172.16-31. > diğer)."""
    if not _is_private_lan_ip(ip_str):
        return 0
    if ip_str.startswith("192.168."):
        return 3
    if ip_str.startswith("10."):
        return 2
    return 1


def _is_local_ip(ip_str: str) -> bool:
    """UDP discovery istemcisinin yerel bir LAN IP'si olup olmadığını doğrular."""
    return _is_private_lan_ip(ip_str)


def _is_virtual_iface(iface_name: str) -> bool:
    """Arayüz adının sanal, VPN, tünel veya loopback olup olmadığını kontrol eder."""
    lower = (iface_name or "").lower()
    return any(kw in lower for kw in _VIRTUAL_IFACE_KEYWORDS)


def _iface_priority(iface_name: str) -> int:
    """Fiziksel Ethernet ve Wi-Fi arayüzlerine öncelik puanı döndürür (-1: sanal/filtreli)."""
    if _is_virtual_iface(iface_name):
        return -1
    lower = (iface_name or "").lower()
    if (
        "ethernet" in lower
        or "yerel ağ" in lower
        or "local area" in lower
        or re.match(r"^(eth|enp|eno|ens|em|en)\d+", lower)
    ):
        return 3
    if (
        "wi-fi" in lower
        or "wifi" in lower
        or "wlan" in lower
        or "wireless" in lower
        or "kablosuz" in lower
        or re.match(r"^wl[a-z0-9]*\d+", lower)
    ):
        return 2
    return 1


def get_network_info(preferred_client_ip=None):
    """
    Aktif yerel IPv4, MAC ve Broadcast adreslerini %100 dinamik olarak tespit eder.
    Sanal ağ kartlarını (WSL, Hyper-V, VirtualBox, VMware, Docker, VPN vb.) filtreler;
    fiziksel Ethernet/Wi-Fi arayüzlerine ve varsa preferred_client_ip alt ağına öncelik verir.
    Herhangi bir ağ kartı bulunamadığı uç durumlarda varsayılan fallback değerlerini döndürür.
    """
    ip = None
    mac = None
    broadcast = None

    client_addr_obj = _parse_usable_ipv4(preferred_client_ip) if preferred_client_ip else None

    # 1. Aşama: psutil ile ağ kartlarını tara ve sanal / fiziksel adayları ayır
    clean_candidates = []
    virtual_candidates = []
    try:
        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()

        for iface_name, addrs in if_addrs.items():
            stats = if_stats.get(iface_name)
            # Sadece ayakta olan arayüzleri değerlendir
            if stats and not stats.isup:
                continue

            iface_mac = None
            for a in addrs:
                if a.family == psutil.AF_LINK and a.address:
                    cleaned = a.address.replace(":", "-").upper()
                    # Boş veya geçersiz MAC'leri ele
                    if cleaned and cleaned != "00-00-00-00-00-00":
                        iface_mac = cleaned
                        break

            iface_prio = _iface_priority(iface_name)

            for a in addrs:
                if a.family != socket.AF_INET or not a.address:
                    continue
                cand_ip = a.address
                if not _parse_usable_ipv4(cand_ip):
                    continue

                cand_bcast = a.broadcast if (a.broadcast and a.broadcast != "0.0.0.0") else None
                cand_net = None
                is_exact_netmask = False
                if a.netmask and "." in a.netmask:
                    try:
                        cand_net = ipaddress.IPv4Network(f"{cand_ip}/{a.netmask}", strict=False)
                        is_exact_netmask = True
                        if not cand_bcast:
                            cand_bcast = str(cand_net.broadcast_address)
                    except Exception:
                        cand_net = None
                if cand_net is None:
                    try:
                        cand_net = ipaddress.IPv4Network(f"{cand_ip}/24", strict=False)
                    except Exception:
                        cand_net = None

                entry = {
                    "iface": iface_name,
                    "ip": cand_ip,
                    "mac": iface_mac,
                    "broadcast": cand_bcast,
                    "network": cand_net,
                    "is_exact_netmask": is_exact_netmask,
                    "iface_priority": iface_prio,
                    "is_private_lan": _is_private_lan_ip(cand_ip),
                    "priv_rank": _private_block_rank(cand_ip),
                    "is_filtered_ip": _is_filtered_ip(cand_ip),
                }
                if iface_prio > 0 and not entry["is_filtered_ip"]:
                    clean_candidates.append(entry)
                else:
                    virtual_candidates.append(entry)
    except Exception:
        pass

    selected = None

    # İstemci IP'si belirtilmişse aynı alt ağdaki fiziksel/temiz arayüzü öncelikle seç
    if client_addr_obj is not None and clean_candidates:
        subnet_matches = [
            c for c in clean_candidates if c["network"] is not None and client_addr_obj in c["network"]
        ]
        if subnet_matches:
            selected = max(
                subnet_matches,
                key=lambda c: (
                    1 if c["is_exact_netmask"] else 0,
                    c["network"].prefixlen,
                    1 if c["is_private_lan"] else 0,
                    c["iface_priority"],
                    1 if c["mac"] else 0,
                    c["priv_rank"],
                ),
            )

    # 2. Aşama: Alt ağ eşleşmesi yoksa aktif yönlendirilen yerel IP için UDP probe
    probe_ips = set()
    fallback_probe_ips = []
    if selected is None:
        clean_ip_set = {c["ip"] for c in clean_candidates}
        virtual_ip_set = {c["ip"] for c in virtual_candidates}

        for probe_target in [("8.8.8.8", 80), ("1.1.1.1", 80), ("192.168.1.1", 80)]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.settimeout(0.3)
                    s.connect(probe_target)
                    candidate_ip = s.getsockname()[0]
                finally:
                    s.close()
            except Exception:
                continue

            if not candidate_ip or _is_filtered_ip(candidate_ip):
                continue
            # Probe sonucu sanal/VPN arayüzüne aitse atlayıp fiziksel arayüzü ara
            if candidate_ip in virtual_ip_set and candidate_ip not in clean_ip_set:
                continue

            if _is_private_lan_ip(candidate_ip):
                probe_ips.add(candidate_ip)
                break
            else:
                if candidate_ip not in fallback_probe_ips:
                    fallback_probe_ips.append(candidate_ip)

        if clean_candidates:
            selected = max(
                clean_candidates,
                key=lambda c: (
                    1 if c["is_private_lan"] else 0,
                    1 if c["ip"] in probe_ips else 0,
                    1 if c["ip"] in fallback_probe_ips else 0,
                    c["iface_priority"],
                    1 if c["mac"] else 0,
                    c["priv_rank"],
                ),
            )
        elif probe_ips:
            ip = next(iter(probe_ips))
        elif fallback_probe_ips:
            ip = fallback_probe_ips[0]
        else:
            # Son çare: Yalnızca filtreli olmayan IP'ye sahip kalan adaylar (ör. Hyper-V External Switch)
            non_filtered_virtual = [c for c in virtual_candidates if not c["is_filtered_ip"]]
            if non_filtered_virtual:
                selected = max(
                    non_filtered_virtual,
                    key=lambda c: (
                        1 if c["is_private_lan"] else 0,
                        1 if c["mac"] else 0,
                        c["priv_rank"],
                    ),
                )

    if selected is not None:
        ip = selected["ip"]
        mac = selected["mac"]
        broadcast = selected["broadcast"]

    # 3. Aşama: MAC hâlâ bulunamadıysa uuid veya getmac fallback
    if not mac:
        try:
            import uuid
            node = uuid.getnode()
            mac_candidate = ":".join(f"{(node >> ele) & 0xff:02X}" for ele in range(40, -1, -8)).replace(":", "-")
            if mac_candidate and mac_candidate != "00-00-00-00-00-00":
                mac = mac_candidate
        except Exception:
            pass

    # 4. Aşama: Broadcast hâlâ bulunamadıysa IP üzerinden /24 fallback
    if ip and not broadcast:
        parts = ip.split(".")
        if len(parts) == 4:
            broadcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"

    # Son Çare (Fallback)
    final_ip = ip or DEFAULT_IP or "127.0.0.1"
    final_mac = mac or DEFAULT_MAC
    final_bcast = broadcast or DEFAULT_BROADCAST

    return final_ip, final_mac, final_bcast


# -------------------------------------------------------------
# Kalıcı Yapılandırma ve Cihaz Adı Çözümleme
# -------------------------------------------------------------
def _clean_device_name(value) -> str:
    """Cihaz adı adayından kontrol karakterlerini temizler, kırpar ve 64 karakterle sınırlar."""
    if not isinstance(value, str):
        return ""
    return _CONTROL_CHAR_RE.sub("", value).strip()[:64].strip()


def _filter_device_username(username) -> str:
    """Kullanıcı adını sistem/sandbox/makine hesaplarından arındırarak döndürür."""
    if not isinstance(username, str):
        return ""
    candidate = _clean_device_name(username)
    if not candidate:
        return ""
    lowered = candidate.lower()
    if (
        lowered in _SYSTEM_USER_EXCLUDE
        or lowered.startswith("codexsandbox")
        or candidate.endswith("$")
    ):
        return ""
    return candidate


def _load_server_config() -> dict:
    """Sunucu yapılandırmasını config.json dosyasından thread-safe ve UTF-8 olarak okur."""
    with _config_lock:
        if not os.path.isfile(CONFIG_PATH):
            return {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _save_server_config(data: dict):
    """Sunucu yapılandırmasını geçici dosyaya yazıp os.replace ile atomik kaydeder."""
    payload = data if isinstance(data, dict) else {}
    with _config_lock:
        config_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
        os.makedirs(config_dir, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=config_dir,
                prefix=".config_",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, CONFIG_PATH)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise


def get_system_hostname() -> str:
    """İşletim sistemi ağ adını döndürür; boş veya alınamazsa 'PC' döner."""
    try:
        host = (socket.gethostname() or "").strip()
    except Exception:
        host = ""
    return host or "PC"


def _get_active_console_username():
    """Aktif konsol oturumundaki kullanıcı adını WTS API üzerinden döndürür (Session 0 uyumlu)."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        wtsapi32 = ctypes.windll.wtsapi32

        kernel32.WTSGetActiveConsoleSessionId.argtypes = []
        kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

        session_id = kernel32.WTSGetActiveConsoleSessionId()
        if session_id is None or session_id == 0xFFFFFFFF:
            return None

        wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
        wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]

        ptr = ctypes.c_void_p()
        bytes_returned = wintypes.DWORD(0)
        queried = False
        try:
            ok = wtsapi32.WTSQuerySessionInformationW(
                0,
                session_id,
                5,
                ctypes.byref(ptr),
                ctypes.byref(bytes_returned),
            )
            if not ok:
                return None
            queried = True
            if not ptr.value:
                return None
            username = ctypes.wstring_at(ptr.value).strip()
            return username or None
        finally:
            if ptr.value or queried:
                try:
                    wtsapi32.WTSFreeMemory(ptr)
                except Exception:
                    pass
    except Exception:
        return None


def get_device_name() -> str:
    """Kullanıcı dostu cihaz adını öncelik sırasına göre çözümler."""
    env_name = _clean_device_name(os.environ.get("PC_KONTROL_DEVICE_NAME", ""))
    if env_name:
        return env_name

    cfg_name = _clean_device_name(_load_server_config().get("device_name"))
    if cfg_name:
        return cfg_name

    raw_host = get_system_hostname()
    if not _GENERIC_HOST_RE.match(raw_host):
        return raw_host

    # Jenerik Windows bilgisayar adlarında aktif/gerçek kullanıcı adını tercih et
    console_user = _filter_device_username(_get_active_console_username())
    if console_user:
        return console_user

    try:
        users = psutil.users() or []
        sorted_users = sorted(
            users,
            key=lambda u: getattr(u, "started", 0) or 0,
            reverse=True,
        )
        for user in sorted_users:
            valid_name = _filter_device_username(getattr(user, "name", None))
            if valid_name:
                return valid_name
    except Exception:
        pass

    env_user = _filter_device_username(os.environ.get("USERNAME"))
    if env_user:
        return env_user

    try:
        gp_user = _filter_device_username(getpass.getuser())
        if gp_user:
            return gp_user
    except Exception:
        pass

    return raw_host


# -------------------------------------------------------------
# UDP Broadcast Discovery Dinleyicisi (Port 8765)
# -------------------------------------------------------------
def start_udp_discovery_server():
    """
    Yerel ağdan gelen 'PC_KONTROL_DISCOVER' UDP broadcast paketlerini dinler ve
    bilgisayarın anlık IP, Port, MAC, Hostname ve Broadcast bilgilerini JSON olarak döndürür.
    Böylece mobil uygulama (APK) sıfır yapılandırmayla bilgisayarı saniyeler içinde otomatik keşfeder.
    """
    global _udp_discovery_started, _udp_running, _udp_socket
    if _udp_discovery_started:
        return
    _udp_discovery_started = True
    _udp_running = True

    def _worker():
        global _udp_socket, _udp_running
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _udp_socket = sock
        # Port çakışmalarını önle ve broadcast dinlemeye izin ver
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass

        try:
            sock.bind(("0.0.0.0", UDP_DISCOVERY_PORT))
            sock.settimeout(1.5)
        except Exception:
            # UDP portu meşgulse çık
            try:
                sock.close()
            except Exception:
                pass
            _udp_socket = None
            return

        while _udp_running:
            try:
                data, addr = sock.recvfrom(2048)
                if not data:
                    continue
                text = data.decode("utf-8", errors="ignore").strip()

                if "PC_KONTROL_DISCOVER" in text:
                    client_ip = addr[0] if isinstance(addr, (tuple, list)) and len(addr) > 0 else None
                    if client_ip and _is_local_ip(client_ip):
                        curr_ip, curr_mac, curr_bcast = get_network_info(preferred_client_ip=client_ip)
                    else:
                        curr_ip, curr_mac, curr_bcast = get_network_info()
                    dev_name = get_device_name()
                    sys_host = get_system_hostname()
                    discovery_payload = {
                        "service": "pc-kontrol",
                        "hostname": dev_name,
                        "device_name": dev_name,
                        "system_hostname": sys_host,
                        "ip": curr_ip,
                        "port": SERVER_PORT,
                        "mac": curr_mac,
                        "broadcast": curr_bcast,
                    }
                    response_bytes = json.dumps(discovery_payload, ensure_ascii=False).encode("utf-8")
                    sock.sendto(response_bytes, addr)
            except socket.timeout:
                continue
            except Exception:
                if not _udp_running:
                    break
                time.sleep(0.3)

        try:
            sock.close()
        except Exception:
            pass
        _udp_socket = None

    discovery_thread = threading.Thread(target=_worker, daemon=True, name="UdpDiscoveryListener")
    discovery_thread.start()


def stop_udp_discovery_server():
    """UDP keşif dinleyicisini temiz şekilde sonlandırır ve soketi kapatır."""
    global _udp_running, _udp_discovery_started, _udp_socket
    _udp_running = False
    _udp_discovery_started = False
    if _udp_socket:
        try:
            _udp_socket.close()
        except Exception:
            pass
        _udp_socket = None


# UDP Keşif Servisini Modül Yüklendiğinde Arka Planda Hemen Başlat
start_udp_discovery_server()
atexit.register(stop_udp_discovery_server)


def format_uptime(seconds: int) -> str:
    """Saniye cinsinden süreyi '3sa 24dk 12sn' formatına çevirir."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}sa {minutes}dk {secs}sn"
    elif minutes > 0:
        return f"{minutes}dk {secs}sn"
    else:
        return f"{secs}sn"


def _normalize_wol_port(port) -> int:
    """WOL hedef portunu doğrular; 1..65535 aralığı dışındaysa varsayılan 9 döndürür."""
    if isinstance(port, bool) or port is None:
        return 9
    try:
        parsed = int(port)
    except (TypeError, ValueError):
        return 9
    return parsed if 1 <= parsed <= 65535 else 9


def send_wol_packet(mac_address: str, broadcast_ip: str, target_ip: str = None, port: int = 9):
    """Belirtilen MAC adresine hedef port ve port 7 üzerinden broadcast/unicast WOL Magic Packet yollar."""
    clean_mac = re.sub(r"[^0-9A-Fa-f]", "", mac_address or "")
    if len(clean_mac) != 12:
        raise ValueError(f"Geçersiz MAC adresi: {mac_address}")

    effective_port = _normalize_wol_port(port)

    mac_bytes = bytes.fromhex(clean_mac)
    magic_packet = b"\xff" * 6 + mac_bytes * 16

    dest_ips = []
    for candidate in (broadcast_ip, "255.255.255.255", target_ip):
        if isinstance(candidate, str):
            cleaned = candidate.strip()
            if cleaned and cleaned not in dest_ips:
                dest_ips.append(cleaned)

    ports = [effective_port]
    if 7 not in ports:
        ports.append(7)

    targets = [(dest_ip, dest_port) for dest_ip in dest_ips for dest_port in ports]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1.0)
    try:
        for attempt in range(3):
            for dest_ip, dest_port in targets:
                try:
                    sock.sendto(magic_packet, (dest_ip, dest_port))
                except Exception:
                    pass
            if attempt < 2:
                time.sleep(0.05)
    finally:
        sock.close()


def run_cmd_no_window(command_list):
    """Windows komutunu arka planda, pencere açmadan çalıştırır."""
    try:
        return subprocess.Popen(
            command_list,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _get_current_session_id():
    """Mevcut sürecin Windows oturum (Session) kimliğini döndürür; hata halinde None döner."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        sid = wintypes.DWORD(0)
        if kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return int(sid.value)
        return None
    except Exception:
        return None


def _enable_shutdown_privilege() -> bool:
    """Mevcut süreç token'ında SeShutdownPrivilege ayrıcalığını etkinleştirir."""
    if os.name != "nt":
        return False
    token_handle = wintypes.HANDLE()
    token_opened = False
    try:
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.LookupPrivilegeValueW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_LUID),
        ]
        advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.POINTER(_TOKEN_PRIVILEGES),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0020 | 0x0008,  # TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY
            ctypes.byref(token_handle),
        ):
            return False
        token_opened = True

        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(luid)):
            return False

        tp = _TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED

        return bool(
            advapi32.AdjustTokenPrivileges(
                token_handle, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None
            )
        )
    except Exception:
        return False
    finally:
        if token_opened or token_handle.value:
            try:
                ctypes.windll.kernel32.CloseHandle(token_handle)
            except Exception:
                pass


def _trigger_system_sleep() -> bool:
    """Bilgisayarı uyku moduna geçirir; önce powrprof API, başarısızsa PowerShell fallback kullanır."""
    if os.name == "nt":
        try:
            _enable_shutdown_privilege()
            powrprof = ctypes.windll.powrprof
            powrprof.SetSuspendState.argtypes = [
                ctypes.c_ubyte,
                ctypes.c_ubyte,
                ctypes.c_ubyte,
            ]
            powrprof.SetSuspendState.restype = ctypes.c_ubyte
            if powrprof.SetSuspendState(False, False, False):
                return True
        except Exception:
            pass

    ps_cmd = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"
    )
    proc = run_cmd_no_window(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd])
    return proc is not None


def _lock_workstation() -> bool:
    """Session 0 Windows Hizmeti veya normal masaüstü oturumunda ekranı güvenli şekilde kilitler."""
    if os.name == "nt":
        session_id = _get_current_session_id()
        if session_id == 0:
            try:
                kernel32 = ctypes.windll.kernel32
                wtsapi32 = ctypes.windll.wtsapi32
                advapi32 = ctypes.windll.advapi32

                kernel32.WTSGetActiveConsoleSessionId.argtypes = []
                kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL

                active_sid = kernel32.WTSGetActiveConsoleSessionId()
                if active_sid not in (None, 0xFFFFFFFF):
                    wtsapi32.WTSQueryUserToken.argtypes = [
                        wintypes.ULONG,
                        ctypes.POINTER(wintypes.HANDLE),
                    ]
                    wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL
                    wtsapi32.WTSDisconnectSession.argtypes = [
                        wintypes.HANDLE,
                        wintypes.DWORD,
                        wintypes.BOOL,
                    ]
                    wtsapi32.WTSDisconnectSession.restype = wintypes.BOOL

                    advapi32.CreateProcessAsUserW.argtypes = [
                        wintypes.HANDLE,
                        wintypes.LPCWSTR,
                        wintypes.LPWSTR,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        wintypes.BOOL,
                        wintypes.DWORD,
                        ctypes.c_void_p,
                        wintypes.LPCWSTR,
                        ctypes.POINTER(_STARTUPINFOW),
                        ctypes.POINTER(_PROCESS_INFORMATION),
                    ]
                    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

                    user_token = wintypes.HANDLE()
                    si = _STARTUPINFOW()
                    si.cb = ctypes.sizeof(_STARTUPINFOW)
                    si.lpDesktop = "winsta0\\default"
                    pi = _PROCESS_INFORMATION()
                    token_opened = False
                    process_created = False

                    try:
                        if wtsapi32.WTSQueryUserToken(active_sid, ctypes.byref(user_token)):
                            token_opened = True
                            rundll_path = r"C:\Windows\System32\rundll32.exe"
                            cmd_buf = ctypes.create_unicode_buffer(
                                f"{rundll_path} user32.dll,LockWorkStation"
                            )
                            if advapi32.CreateProcessAsUserW(
                                user_token,
                                rundll_path,
                                cmd_buf,
                                None,
                                None,
                                False,
                                CREATE_NO_WINDOW,
                                None,
                                None,
                                ctypes.byref(si),
                                ctypes.byref(pi),
                            ):
                                process_created = True
                                return True
                    except Exception:
                        pass
                    finally:
                        for handle, should_close in (
                            (pi.hThread, process_created or bool(pi.hThread)),
                            (pi.hProcess, process_created or bool(pi.hProcess)),
                            (user_token, token_opened or bool(user_token.value)),
                        ):
                            if should_close:
                                try:
                                    kernel32.CloseHandle(handle)
                                except Exception:
                                    pass

                    try:
                        if wtsapi32.WTSDisconnectSession(0, active_sid, False):
                            return True
                    except Exception:
                        pass
            except Exception:
                pass
            return False

        try:
            user32 = ctypes.windll.user32
            user32.LockWorkStation.argtypes = []
            user32.LockWorkStation.restype = wintypes.BOOL
            if user32.LockWorkStation():
                return True
        except Exception:
            pass

    proc = run_cmd_no_window(["rundll32.exe", "user32.dll,LockWorkStation"])
    return proc is not None


# -------------------------------------------------------------
# ASGI HTTP Yanıt Göndericileri (CORS Destekli)
# -------------------------------------------------------------
MAX_REQUEST_BODY_BYTES = 64 * 1024  # 65536 bayt

CORS_HEADERS = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-methods", b"GET, HEAD, POST, OPTIONS, PUT, DELETE"),
    (b"access-control-allow-headers", b"Content-Type, Authorization, X-Requested-With, Accept, Origin, *"),
    (b"access-control-expose-headers", b"*"),
    (b"access-control-max-age", b"86400"),
]


async def send_response(send, status_code: int, headers: list, body: bytes):
    all_headers = list(CORS_HEADERS) + headers
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": all_headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


async def send_json(send, data: dict, status_code: int = 200, head_only: bool = False):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    await send_response(send, status_code, headers, b"" if head_only else body)


async def send_text(send, text: str, status_code: int = 200, head_only: bool = False):
    body = text.encode("utf-8")
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    await send_response(send, status_code, headers, b"" if head_only else body)


async def send_file(
    send,
    file_path: str,
    content_type: str = None,
    attachment_name: str = None,
    head_only: bool = False,
):
    if not os.path.isfile(file_path):
        await send_json(send, {"success": False, "error": "Dosya bulunamadı."}, status_code=404, head_only=head_only)
        return

    try:
        file_size = os.path.getsize(file_path)
    except Exception as e:
        await send_json(send, {"success": False, "error": f"Dosya boyutu okunamadı: {str(e)}"}, status_code=500, head_only=head_only)
        return

    if not content_type:
        guessed, _ = mimetypes.guess_type(file_path)
        content_type = guessed or "application/octet-stream"

    headers = [
        (b"content-type", content_type.encode("ascii", errors="replace")),
        (b"content-length", str(file_size).encode("ascii")),
    ]
    if attachment_name:
        disposition = f'attachment; filename="{attachment_name}"'
        headers.append((b"content-disposition", disposition.encode("utf-8")))

    all_headers = list(CORS_HEADERS) + headers
    if head_only:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": all_headers,
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        return

    try:
        file_obj = open(file_path, "rb")
    except FileNotFoundError:
        await send_json(send, {"success": False, "error": "Dosya bulunamadı."}, status_code=404)
        return
    except Exception as e:
        await send_json(send, {"success": False, "error": f"Dosya okunamadı: {str(e)}"}, status_code=500)
        return

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": all_headers,
        }
    )

    try:
        with file_obj as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    break
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
    except Exception:
        try:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception:
            pass


def _resolve_apk_path() -> str:
    """Birincil APK dosyasını veya varsa eski APK yolunu döndürür."""
    return APK_PATH if os.path.isfile(APK_PATH) else LEGACY_APK_PATH


def _run_shutdown_command(command_list: list):
    """Önce bekleyen Windows kapanma zamanlayıcısını iptal eder, ardından yeni shutdown komutunu çalıştırır."""
    run_cmd_no_window(["shutdown", "/a"])
    time.sleep(0.1)
    run_cmd_no_window(command_list)


async def _read_request_body(scope, receive):
    """İstek gövdesini en fazla MAX_REQUEST_BODY_BYTES (64 KB) olacak şekilde okur."""
    for hdr_name, hdr_val in scope.get("headers", []):
        if hdr_name.lower() == b"content-length":
            try:
                content_length = int(hdr_val.decode("ascii"))
            except (ValueError, UnicodeDecodeError):
                return None, 400, "Geçersiz Content-Length başlığı."
            if content_length < 0:
                return None, 400, "Geçersiz Content-Length başlığı."
            if content_length > MAX_REQUEST_BODY_BYTES:
                return None, 413, "İstek gövdesi çok büyük (maksimum 64 KB)."
            break

    chunks = []
    total_bytes = 0
    while True:
        msg = await receive()
        if msg.get("type") == "http.disconnect":
            return None, None, None
        chunk = msg.get("body", b"")
        total_bytes += len(chunk)
        if total_bytes > MAX_REQUEST_BODY_BYTES:
            return None, 413, "İstek gövdesi çok büyük (maksimum 64 KB)."
        chunks.append(chunk)
        if not msg.get("more_body", False):
            break
    return b"".join(chunks), None, None


# -------------------------------------------------------------
# Saf ASGI Uygulama Gövdesi
# -------------------------------------------------------------
async def app(scope, receive, send):
    global scheduled_shutdown_target

    # Lifespan olayları (Uvicorn açılış ve kapanış döngüsü)
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                stop_background_services()
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    method = scope["method"].upper()
    path = scope["path"]
    is_head = method == "HEAD"
    is_get_or_head = method in ("GET", "HEAD")

    # CORS Preflight istekleri
    if method == "OPTIONS":
        await send_response(send, 204, [(b"content-length", b"0")], b"")
        return

    # İstek gövdesini topla (en fazla 64 KB)
    body_bytes = b""
    if method in ("POST", "PUT", "PATCH"):
        body_bytes, err_status, err_msg = await _read_request_body(scope, receive)
        if body_bytes is None:
            if err_status is not None:
                await send_json(send, {"success": False, "error": err_msg}, err_status)
            return

    # ---------------------------------------------------------
    # ROTA 1: GET/HEAD /api/status
    # ---------------------------------------------------------
    if is_get_or_head and path == "/api/status":
        try:
            ip, mac, broadcast = get_network_info()

            # CPU Metrikleri
            cpu_percent = round(psutil.cpu_percent(interval=None), 1)
            cpu_phys = psutil.cpu_count(logical=False) or 1
            cpu_log = psutil.cpu_count(logical=True) or 1
            freq = psutil.cpu_freq()
            cpu_freq_ghz = round(freq.current / 1000.0, 2) if (freq and freq.current) else None

            # RAM Metrikleri
            vm = psutil.virtual_memory()
            ram_percent = round(vm.percent, 1)
            ram_used_gb = round(vm.used / (1024**3), 2)
            ram_total_gb = round(vm.total / (1024**3), 2)

            # Disk Metrikleri (Dinamik Sabit Disk Taraması ve Geriye Dönük Uyumluluk)
            disks_list = []
            try:
                partitions = psutil.disk_partitions(all=False)
                for p in partitions:
                    # cdrom olmayan ve fstype değeri boş olmayan sabit disk bölümlerini tara
                    if "cdrom" in p.opts.lower() or not p.fstype:
                        continue
                    try:
                        usage = psutil.disk_usage(p.mountpoint)
                        disks_list.append(
                            {
                                "mount": p.mountpoint.rstrip("\\"),
                                "percent": round(usage.percent, 1),
                                "used_gb": round(usage.used / (1024**3), 1),
                                "total_gb": round(usage.total / (1024**3), 1),
                                "free_gb": round(usage.free / (1024**3), 1),
                            }
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            # Geriye dönük uyumluluk için sistem sürücüsü metrikleri
            try:
                sys_drive = os.environ.get("SystemDrive", "C:")
                if not sys_drive.endswith("\\"):
                    sys_drive += "\\"
                disk = psutil.disk_usage(sys_drive)
                disk_percent = round(disk.percent, 1)
                disk_used_gb = round(disk.used / (1024**3), 2)
                disk_total_gb = round(disk.total / (1024**3), 2)
            except Exception:
                if disks_list:
                    disk_percent = disks_list[0]["percent"]
                    disk_used_gb = disks_list[0]["used_gb"]
                    disk_total_gb = disks_list[0]["total_gb"]
                else:
                    disk_percent = 0.0
                    disk_used_gb = 0.0
                    disk_total_gb = 0.0

            # Uptime
            boot_time = psutil.boot_time()
            uptime_seconds = int(time.time() - boot_time)
            uptime_text = format_uptime(uptime_seconds)

            # Zamanlanmış Kapanma Kontrolü
            now_ts = time.time()
            if scheduled_shutdown_target and scheduled_shutdown_target > now_ts:
                rem_sec = int(scheduled_shutdown_target - now_ts)
                sched_info = {
                    "active": True,
                    "target_timestamp": int(scheduled_shutdown_target),
                    "remaining_seconds": rem_sec,
                    "action": "shutdown",
                }
            else:
                scheduled_shutdown_target = None
                sched_info = {
                    "active": False,
                    "target_timestamp": None,
                    "remaining_seconds": 0,
                    "action": None,
                }

            # APK Durumu
            target_apk = _resolve_apk_path()
            apk_exists = os.path.isfile(target_apk)
            apk_size_kb = round(os.path.getsize(target_apk) / 1024) if apk_exists else 0

            # Donanım Sensör Metrikleri (CPU Sıcaklık & GPU)
            hw_data = hardware_monitor.get_data()

            device_name = get_device_name()
            system_hostname = get_system_hostname()
            cfg_name = _load_server_config().get("device_name")
            custom_device_name = _clean_device_name(cfg_name)

            response_data = {
                "online": True,
                "timestamp": int(now_ts),
                "hostname": device_name,
                "device_name": device_name,
                "system_hostname": system_hostname,
                "custom_device_name": custom_device_name,
                "ip_address": ip,
                "mac_address": mac,
                "broadcast_ip": broadcast,
                "port": SERVER_PORT,
                "uptime_seconds": uptime_seconds,
                "uptime_text": uptime_text,
                "cpu_percent": cpu_percent,
                "cpu_cores": {
                    "physical": cpu_phys,
                    "logical": cpu_log,
                },
                "cpu_freq_ghz": cpu_freq_ghz,
                "cpu_temp_c": hw_data.get("cpu_temp_c"),
                "gpu": hw_data.get(
                    "gpu",
                    {
                        "available": False,
                        "name": None,
                        "temp_c": None,
                        "util_percent": None,
                        "mem_used_mb": None,
                        "mem_total_mb": None,
                    },
                ),
                "ram_percent": ram_percent,
                "ram_used_gb": ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "disk_percent": disk_percent,
                "disk_used_gb": disk_used_gb,
                "disk_total_gb": disk_total_gb,
                "disks": disks_list,
                "scheduled_shutdown": sched_info,
                "apk_available": apk_exists,
                "apk_size_kb": apk_size_kb,
            }

            await send_json(send, response_data, 200, head_only=is_head)
            return
        except Exception as e:
            await send_json(send, {"online": False, "error": str(e)}, 500, head_only=is_head)
            return

    # ---------------------------------------------------------
    # ROTA 2: POST /api/power
    # ---------------------------------------------------------
    if method == "POST" and path == "/api/power":
        if body_bytes.strip():
            try:
                payload = json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await send_json(send, {"success": False, "error": "Geçersiz JSON istek gövdesi."}, 400)
                return
        else:
            payload = {}

        if not isinstance(payload, dict):
            await send_json(send, {"success": False, "error": "JSON gövdesi bir nesne (object) olmalıdır."}, 400)
            return

        raw_action = payload.get("action", "")
        if not isinstance(raw_action, str):
            await send_json(send, {"success": False, "error": "Geçersiz 'action' alanı: metin olmalıdır."}, 400)
            return
        action = raw_action.lower().strip()

        power_actions = {
            "shutdown": ("/s", "PC Kontrol Paneli: Bilgisayar kapatiliyor...", "Bilgisayar 4 saniye içinde kapatılıyor..."),
            "restart": ("/r", "PC Kontrol Paneli: Bilgisayar yeniden baslatiliyor...", "Bilgisayar 4 saniye içinde yeniden başlatılıyor..."),
        }
        if action in power_actions:
            scheduled_shutdown_target = None
            flag, cmd_msg, resp_msg = power_actions[action]
            _run_shutdown_command(["shutdown", flag, "/t", "4", "/c", cmd_msg])
            await send_json(send, {"success": True, "message": resp_msg, "action": action})
            return

        elif action == "sleep":
            def trigger_sleep():
                time.sleep(1.5)
                _trigger_system_sleep()

            threading.Thread(target=trigger_sleep, daemon=True).start()
            await send_json(send, {"success": True, "message": "Bilgisayar 1.5 saniye sonra uyku moduna geçiyor...", "action": "sleep"})
            return

        elif action == "lock":
            _lock_workstation()
            await send_json(send, {"success": True, "message": "Bilgisayar kilitlendi.", "action": "lock"})
            return

        elif action == "schedule_shutdown":
            raw_delay = payload.get("delay_minutes", 30)
            if isinstance(raw_delay, bool):
                delay_minutes = 30
            else:
                try:
                    delay_minutes = int(raw_delay)
                except (ValueError, TypeError, OverflowError):
                    delay_minutes = 30
            delay_minutes = max(1, min(10080, delay_minutes))

            seconds = delay_minutes * 60
            # Varsa önceki zamanlayıcıyı iptal et
            _run_shutdown_command(["shutdown", "/s", "/t", str(seconds), "/c", f"PC Kontrol Paneli: {delay_minutes} dakika sonra kapanacak."])
            scheduled_shutdown_target = time.time() + seconds

            await send_json(
                send,
                {
                    "success": True,
                    "message": f"Kapanma {delay_minutes} dakika sonraya zamanlandı.",
                    "action": "schedule_shutdown",
                    "delay_minutes": delay_minutes,
                    "target_timestamp": int(scheduled_shutdown_target),
                    "remaining_seconds": seconds,
                },
            )
            return

        elif action == "cancel_timer":
            run_cmd_no_window(["shutdown", "/a"])
            scheduled_shutdown_target = None
            await send_json(
                send,
                {
                    "success": True,
                    "message": "Zamanlanmış kapanma iptal edildi.",
                    "action": "cancel_timer",
                },
            )
            return

        else:
            await send_json(
                send,
                {
                    "success": False,
                    "error": f"Desteklenmeyen eylem: '{action}'. (Desteklenenler: shutdown, restart, sleep, lock, schedule_shutdown, cancel_timer)",
                },
                400,
            )
            return

    # ---------------------------------------------------------
    # ROTA 3: POST /api/wol
    # ---------------------------------------------------------
    if method == "POST" and path == "/api/wol":
        try:
            payload = json.loads(body_bytes.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        raw_mac = payload.get("mac")
        if raw_mac is not None and not isinstance(raw_mac, str):
            await send_json(send, {"success": False, "error": "Geçersiz 'mac' alanı: metin olmalıdır."}, 400)
            return
        target_mac = raw_mac.strip() if isinstance(raw_mac, str) else ""

        raw_bcast = payload.get("broadcast")
        if raw_bcast is not None and not isinstance(raw_bcast, str):
            await send_json(send, {"success": False, "error": "Geçersiz 'broadcast' alanı: metin olmalıdır."}, 400)
            return
        target_broadcast = raw_bcast.strip() if isinstance(raw_bcast, str) else ""

        if not target_mac:
            _, dyn_mac, dyn_bcast = get_network_info()
            target_mac = (dyn_mac or "").strip()
            if not target_broadcast:
                target_broadcast = (dyn_bcast or DEFAULT_BROADCAST).strip()
        elif not target_broadcast:
            target_broadcast = DEFAULT_BROADCAST

        if not target_mac:
            await send_json(send, {"success": False, "error": "MAC adresi belirtilmedi."}, 400)
            return
        raw_target_ip = payload.get("ip") or payload.get("target_ip")
        target_ip = str(raw_target_ip).strip() if raw_target_ip is not None else None
        if not target_ip:
            target_ip = None

        target_port = _normalize_wol_port(payload.get("port"))

        try:
            send_wol_packet(target_mac, target_broadcast, target_ip=target_ip, port=target_port)
            await send_json(
                send,
                {
                    "success": True,
                    "message": f"Wake-on-LAN sihirli paketi {target_mac} adresine gönderildi.",
                    "mac": target_mac,
                    "broadcast": target_broadcast,
                    "target_ip": target_ip,
                    "port": target_port,
                },
            )
            return
        except Exception as e:
            await send_json(send, {"success": False, "error": f"WOL paketi gönderilemedi: {str(e)}"}, 400)
            return

    # ---------------------------------------------------------
    # POST /api/config
    # ---------------------------------------------------------
    if method == "POST" and path == "/api/config":
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await send_json(send, {"success": False, "error": "Geçersiz JSON istek gövdesi."}, 400)
            return

        if not isinstance(payload, dict):
            await send_json(send, {"success": False, "error": "JSON gövdesi bir nesne (object) olmalıdır."}, 400)
            return

        raw_name = payload.get("device_name")
        if raw_name is not None and not isinstance(raw_name, str):
            await send_json(
                send,
                {"success": False, "error": "Geçersiz 'device_name' alanı: metin veya null olmalıdır."},
                400,
            )
            return

        cleaned = _clean_device_name(raw_name) if raw_name is not None else ""

        try:
            cfg = _load_server_config()
            if cleaned:
                cfg["device_name"] = cleaned
                _save_server_config(cfg)
                msg = f"Cihaz adı '{cleaned}' olarak kaydedildi."
            else:
                cfg.pop("device_name", None)
                if cfg:
                    _save_server_config(cfg)
                else:
                    with _config_lock:
                        if os.path.exists(CONFIG_PATH):
                            try:
                                os.remove(CONFIG_PATH)
                            except FileNotFoundError:
                                pass
                msg = "Özel cihaz adı sıfırlandı; otomatik cihaz adı kullanılacak."
        except Exception as e:
            await send_json(send, {"success": False, "error": f"Yapılandırma kaydedilemedi: {str(e)}"}, 500)
            return

        await send_json(
            send,
            {
                "success": True,
                "device_name": get_device_name(),
                "custom_device_name": cleaned,
                "system_hostname": get_system_hostname(),
                "message": msg,
            },
        )
        return

    # ---------------------------------------------------------
    # ROTA 4: GET/HEAD /download/PC.apk & /download/PC-Kontrol.apk
    # ---------------------------------------------------------
    if is_get_or_head and path in ("/download/PC.apk", "/download/PC-Kontrol.apk"):
        target_apk = _resolve_apk_path()
        if os.path.isfile(target_apk):
            await send_file(
                send,
                target_apk,
                content_type="application/vnd.android.package-archive",
                attachment_name="PC.apk",
                head_only=is_head,
            )
            return
        else:
            await send_json(
                send,
                {
                    "success": False,
                    "error": "APK dosyası henüz derlenmedi veya bulunamadı (Beklenen konum: dist/PC.apk).",
                },
                404,
                head_only=is_head,
            )
            return

    # ---------------------------------------------------------
    # ROTA 5: Statik Dosyalar ve GET/HEAD /
    # ---------------------------------------------------------
    if is_get_or_head:
        if path == "/" or path == "/index.html":
            index_path = os.path.join(STATIC_DIR, "index.html")
            if os.path.isfile(index_path):
                await send_file(send, index_path, content_type="text/html; charset=utf-8", head_only=is_head)
                return
            else:
                await send_text(send, "PC Kontrol Servisi Aktif. (static/index.html bulunamadı)", head_only=is_head)
                return

        if path.startswith("/static/"):
            from urllib.parse import unquote

            rel_path = unquote(path[len("/static/") :]).lstrip("/\\")
            if rel_path and "\x00" not in rel_path and not os.path.splitdrive(rel_path)[0]:
                static_root = os.path.realpath(STATIC_DIR)
                safe_file_path = os.path.realpath(os.path.join(static_root, rel_path))
                # Dizin dışına çıkışı engelle (prefix bypass dahil)
                try:
                    in_static_dir = os.path.commonpath([static_root, safe_file_path]) == static_root
                except ValueError:
                    in_static_dir = False
                if in_static_dir and safe_file_path != static_root and os.path.isfile(safe_file_path):
                    await send_file(send, safe_file_path, head_only=is_head)
                    return

    # Eşleşmeyen rotalar
    await send_json(send, {"error": "Not Found", "path": path}, 404, head_only=is_head)


# -------------------------------------------------------------
# Servis Yaşam Döngüsü ve Doğrudan Çalıştırma Başlatıcısı
# -------------------------------------------------------------
def stop_background_services():
    """Arka plan donanım izleyici ve UDP keşif servislerini hata korumalı şekilde durdurur."""
    for stop_fn in (hardware_monitor.stop, stop_udp_discovery_server):
        try:
            stop_fn()
        except Exception:
            pass


def create_uvicorn_server(host: str = "0.0.0.0", port: int = SERVER_PORT):
    """Windows Hizmeti veya gömülü çalıştırıcılar için yapılandırılmış uvicorn.Server nesnesi döndürür."""
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )
    return uvicorn.Server(config)


def is_port_in_use(port: int) -> bool:
    """Belirtilen portun zaten dinlenip dinlenmediğini test eder."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("  PC GÜÇ VE DURUM KONTROL SERVİSİ")
    print(f"  Port: {SERVER_PORT} | IP: http://0.0.0.0:{SERVER_PORT}")
    print("=" * 60)

    if is_port_in_use(SERVER_PORT):
        print(f"[UYARI] Port {SERVER_PORT} zaten kullanımda! Sunucu zaten çalışıyor olabilir.")
        sys.exit(0)

    try:
        uvicorn_kwargs = {"host": "0.0.0.0", "port": SERVER_PORT, "log_level": "info"}
        if IS_FROZEN or sys.stderr is None:
            uvicorn_kwargs["log_config"] = None
            uvicorn_kwargs["access_log"] = False
        uvicorn.run(app, **uvicorn_kwargs)
    except Exception as ex:
        print(f"[HATA] Sunucu başlatılamadı: {ex}")

