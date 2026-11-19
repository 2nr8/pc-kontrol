# PC Kontrol v2.0

Windows 10 ve Windows 11 bilgisayarlar için **gerçek Windows Hizmeti (`PCKontrolServisi`)**, **Android mobil uygulaması (`PC.apk`)** ve yerel ağ web konsolu ile çalışan sıfır ayarlı uzaktan güç kontrolü ve donanım telemetrisi sistemi.

---

## İndirme ve Kurulum

Sıfırdan format atılmış bir bilgisayarda bile **Python veya herhangi bir ek araç kurmadan** doğrudan kullanabilirsiniz:

👉 **[En Son Sürümü İndir (Releases)](https://github.com/2nr8/pc-kontrol/releases/latest)**

* **Windows Bilgisayar İçin:** `PC-Kontrol-Kurulum.exe`
* **Android Telefon İçin:** `PC.apk` *(veya kurulum sonrası bilgisayarda açılan paneldeki QR kodu okutarak doğrudan indirebilirsiniz)*

---

## 3 Adımda Sıfır Uğraş Kurulum

1. **Bilgisayara Kurun:** `PC-Kontrol-Kurulum.exe` dosyasını çalıştırıp kurulum sihirbazını tamamlayın.
   * Kurulum sırasında **Windows Güvenlik Duvarı** kuralları (`TCP 8765`, `UDP 8765`, `UDP 7/9`), **Wake-on-LAN** ağ kartı ayarları, **PawnIO** CPU sıcaklık sürücüsü ve arka plan **Windows Hizmeti (`PCKontrolServisi`)** otomatik yapılandırılır.
2. **Telefona Yükleyin:** Kurulum bitiminde tarayıcıda açılan paneldeki **QR Kod Göster** (veya **APK İndir**) butonundan `PC.apk` dosyasını telefonunuza yükleyin.
3. **Bağlanın:** Telefonunuz bilgisayarınızla aynı Wi-Fi / yerel ağdayken uygulamayı açın. **Otomatik UDP Ağ Keşfi** bilgisayarı sıfır ayarla saniyeler içinde bulup eşleştirir.

---

## Özellikler

* **Gerçek Windows Hizmeti (`PCKontrolServisi`):** `services.msc` üzerinden yönetilir, Windows açılışında oturum açılmasını beklemeden başlar, olası çökme durumunda kendini otomatik yeniden başlatır.
* **Session 0 Uyumlu Güç Komutları:** Bilgisayar kapatma, yeniden başlatma, uyku modu, ekran kilitleme ve zamanlı kapatma (iptal edilebilir geri sayım).
* **Wake-on-LAN (WoL):** Bilgisayar tamamen kapalıyken veya uykudayken telefondan tek dokunuşla Magic Packet göndererek bilgisayarı açma.
* **Otomatik UDP Ağ Keşfi:** Port `8765` üzerinden yerel ağ yayınını dinleyerek değişen yerel IP adreslerini ve alt ağları otomatik algılar (sanal/VPN arayüzlerini filtreler).
* **Canlı Donanım Telemetrisi:**
  * CPU kullanımı, çekirdek/frekans ve sıcaklık (`LibreHardwareMonitor` + çevrimdışı gömülü `PawnIO` sürücüsü).
  * GPU kullanımı, sıcaklık ve VRAM (`LibreHardwareMonitor` + `nvidia-smi` fallback).
  * RAM kullanımı ve tüm sabit disk bölümlerinin (`C:`, `D:` vb.) canlı doluluk oranı.
* **Özelleştirilebilir Cihaz Adı:** Jenerik Windows adlarında (`DESKTOP-*`, `LAPTOP-*`, `WIN-*`) aktif kullanıcıyı otomatik seçer; istenirse **Ayarlar** modalından kalıcı özel ad tanımlanabilir.
* **Gerçek ISO/IEC 18004 QR Kod:** Harici internet servisine bağlı kalmadan yerel APK indirme adresi için gerçek QR kod üretir.
* **Temiz Kaldırma:** **Program Ekle veya Kaldır** üzerinden Windows Hizmeti, güvenlik duvarı kuralları ve yapılandırma klasörüyle birlikte tek tıkla kaldırılır.

---

## Wake-on-LAN (WoL) BIOS Notu

Kurulum sihirbazı Windows tarafındaki Hızlı Başlatma (Fast Startup) ve ağ kartı güç ayarlarını otomatik yapılandırır. Ancak tamamen kapalı (**S5**) durumdaki bir kasayı ağdan uyandırabilmek için anakart **BIOS / UEFI** ayarlarında şu seçeneklerin açık olması gerekir:
* **Wake on LAN** / **Power On By PCI-E** (veya *Resume by PCI-E Device*): `Enabled`
* **ErP Ready**: `Disabled` (anakart kapalıyken Ethernet portuna güç vermesi için)

---

## Geliştiriciler İçin Kaynaktan Derleme

Projenin tamamını (`PC.apk` + `PCKontrolService.exe` + `PC-Kontrol-Kurulum.exe`) tek komutla derlemek için:

### Gereksinimler
* Python 3.10+ (`psutil`, `uvicorn`, `pillow`, `pythonnet`, `pyinstaller`)
* JDK 8+ (ör. JDK 21) ve Android SDK (`build-tools` + `platforms/android-30`)
* [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`ISCC.exe`)

### Derleme
```bash
python build_release.py
```

Ortam ve araç hazırlığını derlemeden kontrol etmek için:
```bash
python build_release.py --check
```

Android APK imzalama anahtarı ve şifre yönetimi detayları için [android_app/IMZA_KURULUMU.md](android_app/IMZA_KURULUMU.md) dosyasına bakın.

---

## Üçüncü Taraf Bileşenler

* **LibreHardwareMonitor (`LibreHardwareMonitorLib.dll`)** — MPL 2.0 lisansı kapsamında donanım sensörü okumaları için kullanılır.
* **PawnIO (`PawnIO_setup.exe`)** — İsteğe bağlı kurulum görevi olarak CPU sıcaklık sensörü erişimi için dahil edilmiştir.
