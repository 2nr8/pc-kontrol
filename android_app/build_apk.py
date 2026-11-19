# -*- coding: utf-8 -*-
"""
Android APK Derleme ve İmzalama Betiği
"""
import glob
import os
import sys
import shutil
import subprocess
import zipfile
import string
import secrets

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_DIR, "android_app")
WEB_INDEX = os.path.join(PROJECT_DIR, "pc_server", "static", "index.html")
ASSET_INDEX = os.path.join(APP_DIR, "assets", "index.html")
BUILD_DIR = os.path.join(APP_DIR, "build")
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
FINAL_APK = os.path.join(DIST_DIR, "PC.apk")
LEGACY_APK = os.path.join(DIST_DIR, "PC-Kontrol.apk")

EXE_EXT = ".exe" if os.name == "nt" else ""


def _version_key(name):
    parts = []
    for token in name.replace("-", ".").split("."):
        if token.isdigit():
            parts.append((1, int(token), ""))
        else:
            parts.append((0, 0, token.lower()))
    return parts


def _is_valid_jdk(jdk_dir):
    if not jdk_dir:
        return False
    return os.path.isfile(os.path.join(jdk_dir, "bin", f"javac{EXE_EXT}"))


def resolve_jdk_dir():
    """JDK yolunu sirasiyla: D:\\Tools\\jdk-21 -> JAVA_HOME -> PATH (javac) -> bilinen kurulumlar."""
    candidates = [r"D:\Tools\jdk-21"]
    java_home = os.environ.get("JAVA_HOME", "").strip().strip('"')
    if java_home:
        candidates.append(java_home)

    javac_in_path = shutil.which("javac")
    if javac_in_path:
        candidates.append(os.path.dirname(os.path.dirname(os.path.realpath(javac_in_path))))

    search_patterns = [
        r"D:\Tools\jdk*",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Java", "jdk*"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Eclipse Adoptium", "jdk*"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft", "jdk*"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Zulu", "zulu*"),
    ]
    for pattern in search_patterns:
        matches = sorted(glob.glob(pattern), key=lambda p: _version_key(os.path.basename(p)), reverse=True)
        candidates.extend(matches)

    for candidate in candidates:
        if _is_valid_jdk(candidate):
            return os.path.abspath(candidate)
    return java_home or r"D:\Tools\jdk-21"


def _is_valid_build_tools(bt_dir):
    if not bt_dir or not os.path.isdir(bt_dir):
        return False
    return all([
        os.path.isfile(os.path.join(bt_dir, f"aapt2{EXE_EXT}")),
        os.path.isfile(os.path.join(bt_dir, f"zipalign{EXE_EXT}")),
        os.path.isfile(os.path.join(bt_dir, "lib", "d8.jar")),
        os.path.isfile(os.path.join(bt_dir, "lib", "apksigner.jar")),
    ])


def resolve_build_tools(sdk_dir):
    """build-tools/<en-guncel-surum> alt klasorunu veya dogrudan build-tools dizinini cozer."""
    bt_root = os.path.join(sdk_dir, "build-tools")
    if os.path.isdir(bt_root):
        versioned = []
        for entry in os.listdir(bt_root):
            sub_dir = os.path.join(bt_root, entry)
            if os.path.isdir(sub_dir) and _is_valid_build_tools(sub_dir):
                versioned.append((_version_key(entry), sub_dir))
        if versioned:
            versioned.sort(key=lambda item: item[0], reverse=True)
            return versioned[0][1]
        if _is_valid_build_tools(bt_root):
            return bt_root
    return bt_root


def resolve_android_jar(sdk_dir):
    """platforms/android-<api>/android.jar veya dogrudan platforms/android.jar yolunu cozer."""
    platforms_root = os.path.join(sdk_dir, "platforms")
    if os.path.isdir(platforms_root):
        versioned = []
        for entry in os.listdir(platforms_root):
            sub_dir = os.path.join(platforms_root, entry)
            jar_path = os.path.join(sub_dir, "android.jar")
            if os.path.isdir(sub_dir) and os.path.isfile(jar_path):
                versioned.append((_version_key(entry), jar_path))
        if versioned:
            versioned.sort(key=lambda item: item[0], reverse=True)
            return versioned[0][1]
    return os.path.join(platforms_root, "android.jar")


def resolve_sdk_dir():
    """Android SDK dizinini sirasiyla: ANDROID_HOME -> ANDROID_SDK_ROOT -> bilinen yollar."""
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    for var_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(var_name, "").strip().strip('"')
        if val:
            candidates.append(val)
    candidates.append(r"D:\Tools\android-sdk")
    if local_app:
        candidates.append(os.path.join(local_app, "Temp", "opencode", "android-sdk"))
        candidates.append(os.path.join(local_app, "Android", "Sdk"))
    candidates.extend([
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk"),
        r"C:\Android\Sdk",
        r"D:\Android\Sdk",
    ])

    for candidate in candidates:
        if os.path.isdir(candidate) and _is_valid_build_tools(resolve_build_tools(candidate)):
            return os.path.abspath(candidate)
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return candidates[0] if candidates else r"C:\Android\Sdk"


SDK_DIR = resolve_sdk_dir()
BUILD_TOOLS = resolve_build_tools(SDK_DIR)
ANDROID_JAR = resolve_android_jar(SDK_DIR)

AAPT2 = os.path.join(BUILD_TOOLS, f"aapt2{EXE_EXT}")
ZIPALIGN = os.path.join(BUILD_TOOLS, f"zipalign{EXE_EXT}")
D8_JAR = os.path.join(BUILD_TOOLS, "lib", "d8.jar")
APKSIGNER_JAR = os.path.join(BUILD_TOOLS, "lib", "apksigner.jar")

JDK_DIR = resolve_jdk_dir()
JAVAC = os.path.join(JDK_DIR, "bin", f"javac{EXE_EXT}")
KEYTOOL = os.path.join(JDK_DIR, "bin", f"keytool{EXE_EXT}")
JAVA = os.path.join(JDK_DIR, "bin", f"java{EXE_EXT}")

env = os.environ.copy()
if JDK_DIR:
    env["JAVA_HOME"] = JDK_DIR
env["PATH"] = os.pathsep.join([os.path.join(JDK_DIR, "bin"), BUILD_TOOLS, env.get("PATH", "")])

# --- Keystore ve imza sifresi cozumleme (repoya sifre/anahtar girmez) ---
SIGNING_DIR = os.path.join(os.path.expanduser("~"), ".pckontrol")
DEFAULT_KEYSTORE = os.path.join(SIGNING_DIR, "pckontrol.keystore")
DEFAULT_PASS_FILE = os.path.join(SIGNING_DIR, "keystore.pass")
LEGACY_KEYSTORE = os.path.join(APP_DIR, "pckontrol.keystore")
KEY_ALIAS = "pckontrol"


def resolve_keystore():
    """Keystore yolunu sirasiyla: PCKEYSTORE_PATH -> ~/.pckontrol -> eski proje yolu."""
    candidates = []
    env_path = os.environ.get("PCKEYSTORE_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.append(DEFAULT_KEYSTORE)
    candidates.append(LEGACY_KEYSTORE)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return DEFAULT_KEYSTORE  # ilk kurulumda buraya uretilecek


def resolve_keystore_pass(keystore_path):
    """Sifreyi cozer; keystore yoksa ilk kurulum icin yeni sifre uretir."""
    env_pass = os.environ.get("PCKEYSTORE_PASS")
    if env_pass and env_pass.strip():
        return env_pass.strip(), False

    if os.path.isfile(DEFAULT_PASS_FILE):
        with open(DEFAULT_PASS_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value, False

    if not os.path.isfile(keystore_path):
        # Ilk kurulum: yeni anahtar icin rastgele sifre uret ve kaydet
        alphabet = string.ascii_letters + string.digits + "!#$%*+-=?@"
        new_pass = "".join(secrets.choice(alphabet) for _ in range(32))
        os.makedirs(SIGNING_DIR, exist_ok=True)
        with open(DEFAULT_PASS_FILE, "w", encoding="utf-8") as f:
            f.write(new_pass + "\n")
        print("[BILGI] Yeni imza sifresi uretildi ve %s dosyasina kaydedildi." % DEFAULT_PASS_FILE)
        return new_pass, True

    print("\n[HATA] Keystore var ama sifresi bulunamadi: %s" % keystore_path)
    print("       Cozum 1: PCKEYSTORE_PASS ortam degiskenini tanimlayin.")
    print("       Cozum 2: %s dosyasini olusturun (tek satir sifre)." % DEFAULT_PASS_FILE)
    sys.exit(2)


def _mask(cmd):
    out = []
    hide_next = False
    for arg in cmd:
        if hide_next:
            out.append("******")
            hide_next = False
            continue
        if arg in ("-storepass", "-keypass"):
            out.append(arg)
            hide_next = True
            continue
        if isinstance(arg, str) and (arg.startswith("pass:") or arg.startswith("--ks-pass=pass:") or arg.startswith("--key-pass=pass:")):
            out.append(arg.split("pass:")[0] + "pass:******")
            continue
        out.append(arg)
    return out


def run_cmd(cmd, desc):
    print(f"\n[ADIM] {desc}")
    print("Komut:", " ".join(_mask(cmd)) if isinstance(cmd, list) else cmd)
    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.stdout:
        print("[STDOUT]", res.stdout.strip())
    if res.stderr:
        print("[STDERR]", res.stderr.strip())
    if res.returncode != 0:
        raise RuntimeError(f"HATA: {desc} adımı başarısız oldu (Hata Kodu: {res.returncode})")
    print(f"[BAŞARILI] {desc}")

def sync_web_assets():
    """Paketleme oncesi pc_server/static/index.html dosyasini android_app/assets/index.html ile senkronlar."""
    if os.path.isfile(WEB_INDEX):
        os.makedirs(os.path.dirname(ASSET_INDEX), exist_ok=True)
        shutil.copy2(WEB_INDEX, ASSET_INDEX)
        print(f"[ADIM] Web arayuzu senkronize edildi: {WEB_INDEX} -> {ASSET_INDEX}")
    elif not os.path.isfile(ASSET_INDEX):
        raise FileNotFoundError(
            f"Ne web arayuzu ({WEB_INDEX}) ne de Android asset ({ASSET_INDEX}) bulunamadi."
        )
    else:
        print(f"[UYARI] {WEB_INDEX} bulunamadi; mevcut {ASSET_INDEX} kullanilacak.")


def validate_toolchain():
    """JDK ve Android SDK araclarinin varligini dogrular."""
    required = {
        "JDK (javac)": JAVAC,
        "JDK (keytool)": KEYTOOL,
        "JDK (java)": JAVA,
        "Android SDK (android.jar)": ANDROID_JAR,
        "Build Tools (aapt2)": AAPT2,
        "Build Tools (zipalign)": ZIPALIGN,
        "Build Tools (d8.jar)": D8_JAR,
        "Build Tools (apksigner.jar)": APKSIGNER_JAR,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(
            "Derleme araclari eksik (JAVA_HOME / ANDROID_HOME ayarlarini kontrol edin):\n - "
            + "\n - ".join(missing)
        )
    print(f"[BILGI] JDK_DIR     : {JDK_DIR}")
    print(f"[BILGI] SDK_DIR     : {SDK_DIR}")
    print(f"[BILGI] BUILD_TOOLS : {BUILD_TOOLS}")
    print(f"[BILGI] ANDROID_JAR : {ANDROID_JAR}")


def main():
    validate_toolchain()
    sync_web_assets()
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(DIST_DIR, exist_ok=True)

    gen_dir = os.path.join(BUILD_DIR, "gen")
    classes_dir = os.path.join(BUILD_DIR, "classes")
    dex_dir = os.path.join(BUILD_DIR, "dex")
    res_zip = os.path.join(BUILD_DIR, "resources.zip")
    base_apk = os.path.join(BUILD_DIR, "base.apk")
    unaligned_apk = os.path.join(BUILD_DIR, "unaligned.apk")
    aligned_apk = os.path.join(BUILD_DIR, "aligned.apk")
    keystore = resolve_keystore()
    ks_pass, pass_generated = resolve_keystore_pass(keystore)
    env["PCKEYSTORE_PASS"] = ks_pass

    for d in [gen_dir, classes_dir, dex_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)

    # 1. AAPT2 COMPILE
    res_dir = os.path.join(APP_DIR, "res")
    run_cmd([AAPT2, "compile", "--dir", res_dir, "-o", res_zip], "Kaynaklar Derleniyor (aapt2 compile)")

    # 2. AAPT2 LINK
    manifest = os.path.join(APP_DIR, "AndroidManifest.xml")
    run_cmd([
        AAPT2, "link",
        "-I", ANDROID_JAR,
        "--min-sdk-version", "21",
        "--target-sdk-version", "30",
        "--manifest", manifest,
        "--java", gen_dir,
        "-o", base_apk,
        "--auto-add-overlay",
        res_zip
    ], "Manifest ve Kaynaklar Bağlanıyor (aapt2 link, R.java üretimi)")

    # 3. JAVAC DERLEME
    java_files = []
    src_dir = os.path.join(APP_DIR, "src")
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".java"):
                java_files.append(os.path.join(root, f))
    for root, _, files in os.walk(gen_dir):
        for f in files:
            if f.endswith(".java"):
                java_files.append(os.path.join(root, f))

    print(f"Derlenecek Java Dosyaları ({len(java_files)} adet):")
    for jf in java_files:
        print(" -", jf)

    run_cmd([
        JAVAC,
        "--release", "8",
        "-cp", ANDROID_JAR,
        "-d", classes_dir
    ] + java_files, "Java Kaynak Kodları Derleniyor (javac)")

    # 4. D8 DEX DÖNÜŞTÜRME
    class_files = []
    for root, _, files in os.walk(classes_dir):
        for f in files:
            if f.endswith(".class"):
                class_files.append(os.path.join(root, f))
    print(f"DEX'e Dönüştürülecek Sınıf Dosyaları ({len(class_files)} adet)")

    run_cmd([
        JAVA, "-cp", D8_JAR,
        "com.android.tools.r8.D8",
        "--output", dex_dir,
        "--lib", ANDROID_JAR
    ] + class_files, "Dalvik Bayt Koduna Dönüştürülüyor (D8 dexing)")

    dex_file = os.path.join(dex_dir, "classes.dex")
    if not os.path.exists(dex_file):
        raise FileNotFoundError(f"classes.dex oluşturulamadı: {dex_file}")
    print(f"classes.dex boyutu: {os.path.getsize(dex_file)} bytes")

    # 5. APK PAKETLEME (classes.dex ve assets/index.html ekleme)
    print("\n[ADIM] classes.dex ve assets/index.html APK'ya Ekleniyor...")
    shutil.copy2(base_apk, unaligned_apk)

    with zipfile.ZipFile(unaligned_apk, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(dex_file, "classes.dex")
        zf.write(ASSET_INDEX, "assets/index.html")
    print(f"Unaligned APK boyutu: {os.path.getsize(unaligned_apk)} bytes")

    # 6. ZIPALIGN
    if os.path.exists(aligned_apk):
        os.remove(aligned_apk)
    run_cmd([ZIPALIGN, "-f", "-p", "4", unaligned_apk, aligned_apk], "APK 4-Byte Hizalanıyor (zipalign)")

    # 7. KEYSTORE OLUŞTURMA
    if not os.path.exists(keystore):
        os.makedirs(SIGNING_DIR, exist_ok=True)
        run_cmd([
            KEYTOOL, "-genkeypair", "-v",
            "-keystore", keystore,
            "-alias", KEY_ALIAS,
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass:env", "PCKEYSTORE_PASS",
            "-dname", "CN=Yusuf, OU=PCKontrol, O=Home, L=Istanbul, ST=Istanbul, C=TR"
        ], "Yeni İmza Anahtarı Oluşturuluyor (keytool)")
        print(f"[BİLGİ] Keystore oluşturuldu: {keystore}")
    else:
        print("\nMevcut Keystore Kullanılıyor:", keystore)

    # 8. APKSIGNER İLE İMZALAMA (V1 + V2 + V3)
    if os.path.exists(FINAL_APK):
        os.remove(FINAL_APK)

    run_cmd([
        JAVA, "-jar", APKSIGNER_JAR,
        "sign",
        "--ks", keystore,
        "--ks-pass", "env:PCKEYSTORE_PASS",
        "--ks-key-alias", KEY_ALIAS,
        "--key-pass", "env:PCKEYSTORE_PASS",
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        "--out", FINAL_APK,
        aligned_apk
    ], "APK İmzalanıyor (apksigner V1+V2+V3)")

    # 9. DOĞRULAMA (VERIFY)
    run_cmd([
        JAVA, "-jar", APKSIGNER_JAR,
        "verify",
        "--verbose",
        FINAL_APK
    ], "APK İmzası Doğrulanıyor (apksigner verify)")

    # Hem PC.apk hem de PC-Kontrol.apk olarak kaydet
    shutil.copy2(FINAL_APK, LEGACY_APK)

    print(f"\n=======================================================")
    print(f"TEBRİKLER! APK BAŞARIYLA ÜRETİLDİ:")
    print(f"Hedef 1: {FINAL_APK}")
    print(f"Hedef 2: {LEGACY_APK}")
    print(f"Boyut: {round(os.path.getsize(FINAL_APK) / (1024*1024), 2)} MB ({os.path.getsize(FINAL_APK)} bytes)")
    print(f"=======================================================")

if __name__ == "__main__":
    main()
