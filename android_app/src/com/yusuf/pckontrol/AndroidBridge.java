package com.yusuf.pckontrol;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.widget.Toast;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class AndroidBridge {

    private static final String TAG = "AndroidBridge";
    private final Context context;
    private final Handler mainHandler;
    private final ExecutorService executorService;

    public AndroidBridge(Context context) {
        this.context = context.getApplicationContext();
        this.mainHandler = new Handler(Looper.getMainLooper());
        this.executorService = Executors.newCachedThreadPool();
    }

    /**
     * Arka plan iş parçacığı havuzunu ve bekleyen UI geri çağrılarını sonlandırır.
     */
    public void shutdown() {
        mainHandler.removeCallbacksAndMessages(null);
        if (!executorService.isShutdown()) {
            executorService.shutdownNow();
        }
    }

    /**
     * 102 byte Wake-on-LAN Magic Packet oluşturup UDP üzerinden
     * broadcast ve doğrudan IP adreslerine 3 kez arka arkaya fırlatır.
     * (3 parametreli geriye dönük uyumlu metod)
     */
    @JavascriptInterface
    public String wakeOnLan(String macAddress, String broadcastIp, int port) {
        return wakeOnLan(macAddress, broadcastIp, port, null);
    }

    /**
     * Hedef yerel IP adresi de alabilen aşırı yüklenmiş (overloaded) WoL metodu.
     * Sabit IP bağımlılığını tamamen ortadan kaldırır.
     */
    @JavascriptInterface
    public String wakeOnLan(String macAddress, String broadcastIp, int port, String targetIp) {
        try {
            // MAC adresini arka plan iş parçacığı başlamadan önce doğrula
            byte[] macBytes = parseMac(macAddress);
            executorService.execute(new WolWorker(macBytes, broadcastIp, port, targetIp));
            return "OK";
        } catch (IllegalArgumentException e) {
            Log.e(TAG, "WoL MAC format hatası: " + e.getMessage());
            return "ERR: " + e.getMessage();
        } catch (Exception e) {
            Log.e(TAG, "WoL genel hata: " + e.getMessage());
            return "ERR: " + e.getMessage();
        }
    }

    /**
     * Ağdaki PC Kontrol sunucusunu UDP Broadcast ile otomatik keşfeder.
     * Android'in UDP paket filtrelemesini aşmak için WifiManager.MulticastLock kullanır.
     * "PC_KONTROL_DISCOVER" paketini tüm aktif arayüz broadcast adreslerine,
     * genel broadcast'e (255.255.255.255:8765) ve varsa ipucu adresine gönderir.
     * Sunucudan gelen JSON yanıtını String olarak döner; yanıt gelmezse boş string döner.
     */
    @JavascriptInterface
    public String discoverPc(String broadcastHint) {
        WifiManager.MulticastLock multicastLock = acquireMulticastLock();
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);

            byte[] queryBytes = "PC_KONTROL_DISCOVER".getBytes(StandardCharsets.UTF_8);
            List<InetAddress> targets = getBroadcastAddresses(broadcastHint);

            // Paket kaybını azaltmak için hedeflere kısa aralıkla 2 kez yayın gönder
            for (int attempt = 0; attempt < 2; attempt++) {
                for (InetAddress targetAddr : targets) {
                    try {
                        DatagramPacket packet = new DatagramPacket(queryBytes, queryBytes.length, targetAddr, 8765);
                        socket.send(packet);
                    } catch (Exception e) {
                        Log.d(TAG, "Broadcast paketi gönderilemedi (" + targetAddr + "): " + e.getMessage());
                    }
                }
                if (attempt == 0) {
                    try {
                        Thread.sleep(60);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return "";
                    }
                }
            }

            // Timeout dolana kadar yalnızca "pc-kontrol" yanıtını dinle
            final long timeoutNs = 1500L * 1_000_000L;
            long deadlineNs = System.nanoTime() + timeoutNs;
            byte[] recvBuf = new byte[2048];
            while (true) {
                long remainingMs = (deadlineNs - System.nanoTime()) / 1_000_000L;
                if (remainingMs <= 0) {
                    break;
                }
                socket.setSoTimeout((int) remainingMs);
                DatagramPacket recvPacket = new DatagramPacket(recvBuf, recvBuf.length);
                try {
                    socket.receive(recvPacket);
                } catch (SocketTimeoutException e) {
                    break;
                }

                String response = new String(recvPacket.getData(), 0, recvPacket.getLength(), StandardCharsets.UTF_8).trim();
                if (response.contains("pc-kontrol")) {
                    return response;
                }
            }
            return "";
        } catch (Exception e) {
            Log.d(TAG, "discoverPc tamamlandı veya yanıt alınamadı: " + e.getMessage());
            return "";
        } finally {
            releaseMulticastLock(multicastLock);
        }
    }

    /**
     * Cihazın titreşim motorunu tetikler
     */
    @JavascriptInterface
    public void vibrate(int ms) {
        try {
            Vibrator v = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) {
                int duration = ms > 0 ? ms : 50;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(duration, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(duration);
                }
            }
        } catch (Exception ignored) {
        }
    }

    /**
     * JavaScript içinden güvenli (UI thread üzerinde) kısa Toast mesajı gösterir.
     */
    @JavascriptInterface
    public void showToast(final String message) {
        showToast(message, false);
    }

    /**
     * JavaScript içinden güvenli (UI thread üzerinde) Toast mesajı gösterir (kısa / uzun seçenekli).
     */
    @JavascriptInterface
    public void showToast(final String message, final boolean isLong) {
        if (message == null || message.trim().isEmpty()) return;
        mainHandler.post(() -> {
            try {
                Toast.makeText(context, message.trim(), isLong ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Log.w(TAG, "Toast gösterilemedi: " + e.getMessage());
            }
        });
    }

    /**
     * Sistem bildirim çubuğunda bildirim oluşturur (Android 8.0+ NotificationChannel uyumlu).
     */
    @JavascriptInterface
    public void showNotification(final String title, final String content) {
        if (title == null || content == null) return;
        mainHandler.post(() -> {
            try {
                NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
                if (manager == null) return;

                String channelId = "pc_kontrol_notifications";
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    NotificationChannel channel = new NotificationChannel(
                            channelId,
                            "PC Kontrol Bildirimleri",
                            NotificationManager.IMPORTANCE_DEFAULT
                    );
                    channel.setDescription("PC Kontrol durum ve uyarı bildirimleri");
                    manager.createNotificationChannel(channel);
                }

                Notification.Builder builder;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    builder = new Notification.Builder(context, channelId);
                } else {
                    builder = new Notification.Builder(context);
                }

                builder.setContentTitle(title)
                       .setContentText(content)
                       .setSmallIcon(android.R.drawable.ic_dialog_info)
                       .setAutoCancel(true);

                manager.notify((int) (System.currentTimeMillis() & 0xFFFFFFF), builder.build());
            } catch (Exception e) {
                Log.w(TAG, "Bildirim oluşturulamadı: " + e.getMessage());
            }
        });
    }

    /**
     * Uygulama sürüm adını döner.
     */
    @JavascriptInterface
    public String getAppVersion() {
        try {
            return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "1.6";
        }
    }

    /**
     * Cihaz model bilgisini döner.
     */
    @JavascriptInterface
    public String getDeviceModel() {
        return Build.MANUFACTURER + " " + Build.MODEL;
    }

    /**
     * Cihazın geçerli yerel IP adresini döner (bulunamazsa boş string döner).
     * Wi-Fi (wlan), Ethernet (eth) ve Hotspot (ap) arayüzlerine öncelik vererek
     * VPN (tun0) veya mobil veri (rmnet) arayüzlerinin yanlışlıkla seçilmesini önler.
     */
    @JavascriptInterface
    public String getLocalIpAddress() {
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            if (interfaces == null) {
                return "";
            }

            String bestIp = "";
            int bestPriority = Integer.MIN_VALUE;

            for (NetworkInterface intf : Collections.list(interfaces)) {
                if (intf.isLoopback() || !intf.isUp()) {
                    continue;
                }

                String name = intf.getName() != null ? intf.getName().toLowerCase(Locale.ROOT) : "";
                int ifacePriority = getInterfacePriority(name, intf);
                if (ifacePriority < 0) {
                    continue;
                }

                for (InterfaceAddress addr : intf.getInterfaceAddresses()) {
                    InetAddress inet = addr.getAddress();
                    if (inet instanceof Inet4Address && !inet.isLoopbackAddress()) {
                        int candidatePriority = ifacePriority + (inet.isLinkLocalAddress() ? 0 : 1000);
                        if (candidatePriority > bestPriority) {
                            bestPriority = candidatePriority;
                            bestIp = inet.getHostAddress();
                        }
                    }
                }
            }
            return bestIp != null ? bestIp : "";
        } catch (Exception ignored) {
        }
        return "";
    }

    private static int getInterfacePriority(String name, NetworkInterface intf) {
        if (name.startsWith("wlan")) {
            return 100;
        }
        if (name.startsWith("eth")) {
            return 80;
        }
        if (name.startsWith("ap") || name.startsWith("swlan")) {
            return 60;
        }
        // VPN ve mobil veri arayüzlerini ele
        if (name.startsWith("tun") || name.startsWith("tap") || name.startsWith("ppp")
                || name.startsWith("wg") || name.startsWith("ipsec") || name.startsWith("vti")
                || name.startsWith("rmnet") || name.startsWith("ccmni") || name.startsWith("pdp")
                || name.startsWith("clat") || name.startsWith("v4-") || name.startsWith("dummy")) {
            return -1;
        }
        try {
            if (intf.isPointToPoint()) {
                return -1;
            }
        } catch (Exception ignored) {
        }
        return 20;
    }

    /**
     * Android WiFi çipsetlerinin gelen UDP yayınlarını filtrelemesini engellemek için MulticastLock edinir.
     */
    private WifiManager.MulticastLock acquireMulticastLock() {
        try {
            WifiManager wifiManager = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
            if (wifiManager != null) {
                WifiManager.MulticastLock lock = wifiManager.createMulticastLock("pckontrol_udp_lock");
                lock.setReferenceCounted(true);
                lock.acquire();
                return lock;
            }
        } catch (Exception e) {
            Log.w(TAG, "MulticastLock alınırken uyarı: " + e.getMessage());
        }
        return null;
    }

    /**
     * Edinilen MulticastLock'ı güvenle serbest bırakır.
     */
    private void releaseMulticastLock(WifiManager.MulticastLock lock) {
        if (lock != null && lock.isHeld()) {
            try {
                lock.release();
            } catch (Exception ignored) {
            }
        }
    }

    /**
     * Aktif tüm ağ arabirimlerinin broadcast adreslerini ve küresel broadcast adresini toplar.
     */
    private static List<InetAddress> getBroadcastAddresses(String hintIp) {
        Set<InetAddress> addresses = new HashSet<>();
        try {
            // 1. Varsa kullanıcı/istemci ipucu adresi
            if (hintIp != null && !hintIp.trim().isEmpty()) {
                try {
                    addresses.add(InetAddress.getByName(hintIp.trim()));
                } catch (Exception ignored) {
                }
            }

            // 2. Küresel broadcast
            try {
                addresses.add(InetAddress.getByName("255.255.255.255"));
            } catch (Exception ignored) {
            }

            // 3. Ağ arayüzlerinden hesaplanan broadcast adresleri
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            if (interfaces != null) {
                for (NetworkInterface networkInterface : Collections.list(interfaces)) {
                    if (networkInterface.isLoopback() || !networkInterface.isUp()) {
                        continue;
                    }
                    for (InterfaceAddress interfaceAddress : networkInterface.getInterfaceAddresses()) {
                        InetAddress bcast = interfaceAddress.getBroadcast();
                        if (bcast != null) {
                            addresses.add(bcast);
                        }
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "Broadcast adresleri toplanırken hata: " + e.getMessage());
        }
        return new ArrayList<>(addresses);
    }

    /**
     * MAC adresi dizesini 6 baytlık diziye dönüştürür.
     */
    private static byte[] parseMac(String macStr) throws IllegalArgumentException {
        if (macStr == null) {
            throw new IllegalArgumentException("MAC adresi boş.");
        }
        String clean = macStr.replaceAll("[^0-9A-Fa-f]", "");
        if (clean.length() != 12) {
            throw new IllegalArgumentException("Geçersiz MAC adresi: " + macStr);
        }
        byte[] bytes = new byte[6];
        for (int i = 0; i < 6; i++) {
            bytes[i] = (byte) Integer.parseInt(clean.substring(i * 2, (i * 2) + 2), 16);
        }
        return bytes;
    }

    /**
     * Arka planda UDP üzerinden Wake-on-LAN Magic Paketlerini yollayan iş parçacığı görevi.
     */
    private static class WolWorker implements Runnable {
        private final byte[] macBytes;
        private final String broadcastIp;
        private final int port;
        private final String targetIp;

        public WolWorker(byte[] macBytes, String broadcastIp, int port, String targetIp) {
            this.macBytes = macBytes;
            this.broadcastIp = broadcastIp;
            this.port = port > 0 ? port : 9;
            this.targetIp = targetIp;
        }

        @Override
        public void run() {
            try {
                byte[] magicPacket = new byte[102];

                // İlk 6 bayt: 0xFF
                for (int i = 0; i < 6; i++) {
                    magicPacket[i] = (byte) 0xFF;
                }
                // Sonraki 16 tekrar: Hedef cihazın MAC adresi (16 * 6 = 96 bayt)
                for (int i = 0; i < 16; i++) {
                    System.arraycopy(macBytes, 0, magicPacket, 6 + (i * 6), 6);
                }

                List<InetAddress> targets = getBroadcastAddresses(broadcastIp);

                // Arka arkaya 3 kez gönderim (paket kaybını önleme)
                for (int attempt = 0; attempt < 3; attempt++) {
                    try (DatagramSocket socket = new DatagramSocket()) {
                        socket.setBroadcast(true);

                        // Broadcast hedefleri: port ve standart port 7
                        for (InetAddress targetAddr : targets) {
                            sendPacket(socket, magicPacket, targetAddr, port);
                            if (port != 7) {
                                sendPacket(socket, magicPacket, targetAddr, 7);
                            }
                        }

                        // Doğrudan hedef yerel IP (varsa doğrudan unicast fırlat)
                        if (targetIp != null && !targetIp.trim().isEmpty()) {
                            try {
                                InetAddress directAddr = InetAddress.getByName(targetIp.trim());
                                sendPacket(socket, magicPacket, directAddr, port);
                                if (port != 7) {
                                    sendPacket(socket, magicPacket, directAddr, 7);
                                }
                            } catch (Exception ignored) {
                            }
                        }
                    }

                    if (attempt < 2) {
                        try {
                            Thread.sleep(120);
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                            return;
                        }
                    }
                }
                Log.d(TAG, "WoL sihirli paketleri başarıyla yollandı.");
            } catch (Exception e) {
                Log.e(TAG, "WoL gönderimi sırasında hata oluştu: " + e.getMessage(), e);
            }
        }

        private void sendPacket(DatagramSocket socket, byte[] packetData, InetAddress address, int targetPort) {
            try {
                if (address != null) {
                    DatagramPacket packet = new DatagramPacket(packetData, packetData.length, address, targetPort);
                    socket.send(packet);
                }
            } catch (Exception ignored) {
            }
        }
    }
}
