# APK İmzalama Kurulumu

Bu projede **imza anahtarı (keystore) ve şifresi repoya girmez**. Repo public hale gelse bile anahtar sızmaz.

## Anahtar nerede?

Keystore proje dışında, kullanıcı profilinde tutulur:

```
%USERPROFILE%\.pckontrol\pckontrol.keystore
%USERPROFILE%\.pckontrol\keystore.pass
```

`build_apk.py` anahtarı şu sırayla arar:

1. `PCKEYSTORE_PATH` ortam değişkeni
2. `%USERPROFILE%\.pckontrol\pckontrol.keystore`
3. (eski kurulumlar için) `android_app\pckontrol.keystore`

## Şifre nereden okunur?

1. `PCKEYSTORE_PASS` ortam değişkeni
2. `%USERPROFILE%\.pckontrol\keystore.pass` dosyası (tek satır)

İkisi de yoksa betik net bir hata mesajıyla durur; şifre koda gömülü değildir.

Şifre hiçbir zaman komut satırı argümanı olarak geçmez: alt süreçlere `PCKEYSTORE_PASS` ortam değişkeni üzerinden verilir (`apksigner --ks-pass env:PCKEYSTORE_PASS`, `keytool -storepass:env`) ve konsol çıktısında maskelenir.

## Yeni makinede derleme

1. Anahtarı ve şifre dosyasını `%USERPROFILE%\.pckontrol\` altına kopyala.
   - Anahtar yoksa betik ilk çalıştırmada yeni anahtar üretir ve şifreyi `keystore.pass` dosyasına yazar.
2. `python android_app\build_apk.py`

> **Dikkat:** Yeni anahtar üretilirse imza kimliği değişir. Telefonda eski APK'nın üzerine kurulamaz; önce kaldırıp sonra kurmak gerekir.

## Yedekleme

`%USERPROFILE%\.pckontrol\` klasörünü (anahtar + şifre) güvenli bir yere yedekle. Bu klasör kaybolursa mevcut APK'ları güncelleyemezsin, yeni imza kimliğiyle yeniden kurulum gerekir.
