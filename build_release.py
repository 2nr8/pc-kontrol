# -*- coding: utf-8 -*-
"""
PC Kontrol Tek Komutlu Yayın Derleme Hattı (Release Pipeline).

Adımlar:
1. installer/pc_kontrol.ico dosyasını (yoksa veya ic_launcher.png daha yeniyse) üretir.
2. android_app/build_apk.py ile güncel dist/PC.apk dosyasını derler ve doğrular.
3. installer/redist/PawnIO_setup.exe dosyasını kontrol/indirir ve SHA256 doğrular.
4. PyInstaller ile windows_service/PCKontrolService.spec derler -> build_dist/PCKontrolService/PCKontrolService.exe.
5. Inno Setup 6 (ISCC.exe) ile installer/PCKontrol.iss derler -> dist/PC-Kontrol-Kurulum.exe.
6. Tüm çıktı dosyalarının yollarını ve boyutlarını raporlar.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

ICON_SRC = os.path.join(ROOT_DIR, "android_app", "res", "drawable", "ic_launcher.png")
ICON_DST = os.path.join(ROOT_DIR, "installer", "pc_kontrol.ico")
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

APK_BUILD_SCRIPT = os.path.join(ROOT_DIR, "android_app", "build_apk.py")
APK_OUTPUT = os.path.join(ROOT_DIR, "dist", "PC.apk")

PAWNIO_URL = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
PAWNIO_SHA256 = "1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032"
PAWNIO_PATH = os.path.join(ROOT_DIR, "installer", "redist", "PawnIO_setup.exe")

PYINSTALLER_SPEC = os.path.join(ROOT_DIR, "windows_service", "PCKontrolService.spec")
BUILD_DIST_DIR = os.path.join(ROOT_DIR, "build_dist")
BUILD_WORK_DIR = os.path.join(ROOT_DIR, "build_work")
SERVICE_EXE_OUTPUT = os.path.join(BUILD_DIST_DIR, "PCKontrolService", "PCKontrolService.exe")

INNO_SCRIPT = os.path.join(ROOT_DIR, "installer", "PCKontrol.iss")
INSTALLER_OUTPUT = os.path.join(ROOT_DIR, "dist", "PC-Kontrol-Kurulum.exe")


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB ({num_bytes:,} B)"
    return f"{num_bytes / (1024 * 1024):.2f} MB ({num_bytes:,} B)"


def _compute_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper()


def _run_step(cmd: list[str], title: str) -> None:
    print(f"\n[ADIM] {title}")
    print("Komut:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"{title} başarısız oldu (çıkış kodu: {result.returncode}).")


def ensure_icon() -> str:
    """installer/pc_kontrol.ico yoksa veya kaynak PNG daha yeniyse yeniden oluşturur."""
    print("\n[1/5] Uygulama ikonu kontrol ediliyor...")
    if not os.path.isfile(ICON_SRC):
        raise FileNotFoundError(f"Kaynak ikon bulunamadı: {ICON_SRC}")

    needs_build = not os.path.isfile(ICON_DST)
    if not needs_build:
        src_mtime = os.path.getmtime(ICON_SRC)
        dst_mtime = os.path.getmtime(ICON_DST)
        if src_mtime > dst_mtime:
            needs_build = True

    if needs_build:
        from PIL import Image

        os.makedirs(os.path.dirname(ICON_DST), exist_ok=True)
        with Image.open(ICON_SRC) as src_img:
            rgba = src_img.convert("RGBA")
            rgba.save(ICON_DST, format="ICO", sizes=ICO_SIZES)
        print(f"  [+] İkon üretildi: {ICON_DST} ({_format_size(os.path.getsize(ICON_DST))})")
    else:
        print(f"  [=] İkon güncel: {ICON_DST} ({_format_size(os.path.getsize(ICON_DST))})")

    return ICON_DST


def build_android_apk() -> str:
    """android_app/build_apk.py çalıştırarak dist/PC.apk üretir ve doğrular."""
    print("\n[2/5] Android APK derleniyor...")
    if not os.path.isfile(APK_BUILD_SCRIPT):
        raise FileNotFoundError(f"APK derleme betiği bulunamadı: {APK_BUILD_SCRIPT}")

    _run_step([sys.executable, APK_BUILD_SCRIPT], "Android APK Derleme")

    if not os.path.isfile(APK_OUTPUT) or os.path.getsize(APK_OUTPUT) <= 0:
        raise FileNotFoundError(f"Beklenen APK çıktısı üretilemedi veya boş: {APK_OUTPUT}")

    print(f"  [+] APK doğrulandı: {APK_OUTPUT} ({_format_size(os.path.getsize(APK_OUTPUT))})")
    return APK_OUTPUT


def ensure_pawnio_redist() -> str:
    """PawnIO kurulum dosyasını kontrol eder, yoksa indirir ve SHA256 doğrular."""
    print("\n[3/5] PawnIO sürücü paketi kontrol ediliyor...")
    os.makedirs(os.path.dirname(PAWNIO_PATH), exist_ok=True)

    if not os.path.isfile(PAWNIO_PATH):
        print(f"  [+] İndiriliyor: {PAWNIO_URL}")
        tmp_path = PAWNIO_PATH + ".download"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        req = urllib.request.Request(PAWNIO_URL, headers={"User-Agent": "PCKontrolReleaseBuilder/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as out_file:
                shutil.copyfileobj(resp, out_file)
            os.replace(tmp_path, PAWNIO_PATH)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    actual_sha = _compute_sha256(PAWNIO_PATH)
    if actual_sha != PAWNIO_SHA256:
        try:
            os.remove(PAWNIO_PATH)
        except OSError:
            pass
        raise RuntimeError(
            "PawnIO_setup.exe SHA256 doğrulaması başarısız! Dosya silindi.\n"
            f"  Beklenen : {PAWNIO_SHA256}\n"
            f"  Bulunan  : {actual_sha}"
        )

    print(
        f"  [+] PawnIO doğrulandı (SHA256 OK): {PAWNIO_PATH} "
        f"({_format_size(os.path.getsize(PAWNIO_PATH))})"
    )
    return PAWNIO_PATH


def resolve_pyinstaller_cmd() -> list[str] | None:
    if importlib.util.find_spec("PyInstaller") is not None:
        return [sys.executable, "-m", "PyInstaller"]
    exe_in_path = shutil.which("pyinstaller")
    if exe_in_path:
        return [exe_in_path]
    return None


def build_windows_service() -> str:
    """PyInstaller ile PCKontrolService.exe (onedir) paketini derler."""
    print("\n[4/5] PyInstaller ile Windows Hizmeti derleniyor...")
    if not os.path.isfile(PYINSTALLER_SPEC):
        raise FileNotFoundError(f"PyInstaller spec dosyası bulunamadı: {PYINSTALLER_SPEC}")

    pyinstaller_cmd = resolve_pyinstaller_cmd()
    if pyinstaller_cmd is None:
        raise RuntimeError(
            "PyInstaller kurulu değil! Kurmak için: python -m pip install pyinstaller"
        )

    cmd = pyinstaller_cmd + [
        "--noconfirm",
        "--clean",
        "--distpath",
        BUILD_DIST_DIR,
        "--workpath",
        BUILD_WORK_DIR,
        PYINSTALLER_SPEC,
    ]
    _run_step(cmd, "PyInstaller Windows Hizmeti Derleme")

    if not os.path.isfile(SERVICE_EXE_OUTPUT) or os.path.getsize(SERVICE_EXE_OUTPUT) <= 0:
        raise FileNotFoundError(f"PyInstaller çıktısı bulunamadı: {SERVICE_EXE_OUTPUT}")

    print(
        f"  [+] Servis EXE doğrulandı: {SERVICE_EXE_OUTPUT} "
        f"({_format_size(os.path.getsize(SERVICE_EXE_OUTPUT))})"
    )
    return SERVICE_EXE_OUTPUT


def find_iscc() -> str | None:
    """Inno Setup 6 derleyicisini (ISCC.exe) bilinen konumlarda ve PATH'te arar."""
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    candidates = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    if local_app_data:
        candidates.append(os.path.join(local_app_data, "Programs", "Inno Setup 6", "ISCC.exe"))

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    for name in ("ISCC.exe", "iscc"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found

    return None


def build_installer() -> str:
    """Inno Setup 6 (ISCC.exe) ile tek dosya kurulum paketini derler."""
    print("\n[5/5] Inno Setup 6 ile kurulum paketi derleniyor...")
    if not os.path.isfile(INNO_SCRIPT):
        raise FileNotFoundError(f"Inno Setup betiği bulunamadı: {INNO_SCRIPT}")

    iscc = find_iscc()
    if not iscc:
        raise FileNotFoundError(
            "Inno Setup 6 (ISCC.exe) bulunamadı. Aranan konumlar:\n"
            r"  - C:\Program Files (x86)\Inno Setup 6\ISCC.exe" + "\n"
            r"  - C:\Program Files\Inno Setup 6\ISCC.exe" + "\n"
            r"  - %LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" + "\n"
            "  - PATH"
        )

    _run_step([iscc, INNO_SCRIPT], "Inno Setup Kurulum Paketi Derleme")

    if not os.path.isfile(INSTALLER_OUTPUT) or os.path.getsize(INSTALLER_OUTPUT) <= 0:
        raise FileNotFoundError(f"Kurulum EXE çıktısı üretilemedi: {INSTALLER_OUTPUT}")

    print(
        f"  [+] Kurulum EXE doğrulandı: {INSTALLER_OUTPUT} "
        f"({_format_size(os.path.getsize(INSTALLER_OUTPUT))})"
    )
    return INSTALLER_OUTPUT


def check_environment() -> int:
    """Tam derleme çalıştırmadan gerekli dosya ve araç durumunu raporlar."""
    print("PC Kontrol Yayın Derleme Ortam Kontrolü")
    print("=" * 60)
    checks = [
        ("Kaynak İkon", os.path.isfile(ICON_SRC), ICON_SRC),
        ("Hedef ICO", os.path.isfile(ICON_DST), ICON_DST),
        ("APK Derleme Betiği", os.path.isfile(APK_BUILD_SCRIPT), APK_BUILD_SCRIPT),
        ("PyInstaller Spec", os.path.isfile(PYINSTALLER_SPEC), PYINSTALLER_SPEC),
        ("Inno Setup Betiği", os.path.isfile(INNO_SCRIPT), INNO_SCRIPT),
        ("WoL PowerShell Betiği", os.path.isfile(os.path.join(ROOT_DIR, "installer", "setup_network_wol.ps1")), os.path.join(ROOT_DIR, "installer", "setup_network_wol.ps1")),
        ("PyInstaller Modülü", resolve_pyinstaller_cmd() is not None, str(resolve_pyinstaller_cmd() or "Eksik")),
        ("Inno Setup (ISCC.exe)", find_iscc() is not None, str(find_iscc() or "Eksik")),
    ]
    for label, ok, detail in checks:
        status = "HAZIR" if ok else "EKSİK"
        print(f"  [{status:5}] {label:<24}: {detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PC Kontrol Yayın Derleme Hattı")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Derleme yapmadan sadece araç ve dosya hazırlığını kontrol eder.",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_environment()

    print("=" * 64)
    print("  PC KONTROL - YAYIN DERLEME HATTI BAŞLATILIYOR")
    print("=" * 64)

    icon_path = ensure_icon()
    apk_path = build_android_apk()
    pawnio_path = ensure_pawnio_redist()
    service_exe = build_windows_service()
    installer_exe = build_installer()

    artifacts = [
        ("İkon (ICO)", icon_path),
        ("Android APK", apk_path),
        ("PawnIO Kurucu", pawnio_path),
        ("Servis EXE", service_exe),
        ("Kurulum Paketi", installer_exe),
    ]

    print("\n" + "=" * 64)
    print("  YAYIN DERLEMESİ BAŞARIYLA TAMAMLANDI!")
    print("=" * 64)
    for label, path in artifacts:
        size_str = _format_size(os.path.getsize(path))
        print(f"  {label:<16}: {path} [{size_str}]")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[HATA] Yayın derlemesi başarısız: {exc}", file=sys.stderr)
        sys.exit(1)
