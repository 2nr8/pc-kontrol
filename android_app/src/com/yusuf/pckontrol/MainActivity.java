package com.yusuf.pckontrol;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JsResult;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

public class MainActivity extends Activity {

    private static final String TAG = "PCKontrolActivity";
    private WebView webView;
    private AndroidBridge androidBridge;
    private long backPressedTime = 0;
    private Toast exitToast;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        super.onCreate(savedInstanceState);

        // Koyu durum çubuğu ve navigasyon çubuğu ayarları (#0A0A0B)
        Window window = getWindow();
        window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
            window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
            window.setStatusBarColor(0xFF0A0A0B);
            window.setNavigationBarColor(0xFF0A0A0B);
        }

        // Açılışta beyaz parlamayı engellemek için pencere arka planı
        window.getDecorView().setBackgroundColor(0xFF0A0A0B);

        // FrameLayout Kapsayıcısı ve WebView Başlatma (Tam ekran MATCH_PARENT garantisi)
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF0A0A0B);
        root.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        webView = new WebView(this);
        webView.setBackgroundColor(0xFF0A0A0B);
        webView.setLayoutParams(new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
        root.addView(webView);

        setContentView(root, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // WebView Ayarları
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true); // localStorage için kritik
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);

        // Ekran boyutu ve ölçekleme ayarları
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setTextZoom(100); // Sistem yazı tipi boyutunun arayüzü bozmasını önler
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);

        // file:///android_asset/ içinden yerel ağ HTTP API'sine fetch/CORS yapabilmek için ÇOK KRİTİK
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setAllowUniversalAccessFromFileURLs(true);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        // Önbellek ve optimizasyon
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR1) {
            settings.setMediaPlaybackRequiresUserGesture(false);
        }

        // Donanım hızlandırma ve WebView istemcileri
        webView.setScrollBarStyle(View.SCROLLBARS_INSIDE_OVERLAY);
        webView.setWebViewClient(new AppWebViewClient(this));
        webView.setWebChromeClient(new AppWebChromeClient(this));

        // JavaScript Köprüsü (AndroidBridge)
        androidBridge = new AndroidBridge(this);
        webView.addJavascriptInterface(androidBridge, "AndroidBridge");

        // Android 13+ (API 33+) Predictive Back / OnBackInvokedCallback desteği
        setupModernBackHandling();

        // Yerel varlık yükleme
        webView.loadUrl("file:///android_asset/index.html");
    }

    private static class AppWebViewClient extends WebViewClient {
        private final MainActivity activity;

        public AppWebViewClient(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            if (url == null) return false;
            // Uygulama içi varlıklar ve yerel/uzak HTTP bağlantıları WebView içinde açılsın
            if (url.startsWith("file://") || url.startsWith("http://") || url.startsWith("https://")) {
                return false;
            }
            // Dış şemalar (mailto, tel, market vb.) için harici intent başlat
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                activity.startActivity(intent);
                return true;
            } catch (Exception e) {
                Log.w(TAG, "Harici URL açılamadı: " + url, e);
                return false;
            }
        }

        @Override
        public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
            super.onReceivedError(view, errorCode, description, failingUrl);
            Log.e(TAG, "WebView yükleme hatası [" + errorCode + "]: " + description + " URL: " + failingUrl);
            activity.handleLoadError(view, failingUrl, description);
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            super.onReceivedError(view, request, error);
            if (request != null && request.isForMainFrame()) {
                String desc = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && error != null && error.getDescription() != null)
                        ? error.getDescription().toString() : "Sayfa yüklenemedi";
                String url = request.getUrl() != null ? request.getUrl().toString() : "";
                Log.e(TAG, "Ana çerçeve yükleme hatası: " + desc + " URL: " + url);
                activity.handleLoadError(view, url, desc);
            }
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse errorResponse) {
            super.onReceivedHttpError(view, request, errorResponse);
            if (request != null && request.isForMainFrame()) {
                int statusCode = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP && errorResponse != null)
                        ? errorResponse.getStatusCode() : -1;
                Log.e(TAG, "HTTP hatası: " + statusCode + " URL: " + request.getUrl());
            }
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            Log.e(TAG, "SSL sertifika uyarısı: " + error);
            super.onReceivedSslError(view, handler, error);
        }
    }

    private static class AppWebChromeClient extends WebChromeClient {
        private final MainActivity activity;

        public AppWebChromeClient(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public boolean onConsoleMessage(ConsoleMessage consoleMessage) {
            if (consoleMessage != null) {
                String logText = "[JS " + consoleMessage.messageLevel() + "] "
                        + consoleMessage.message() + " (" + consoleMessage.sourceId()
                        + ":" + consoleMessage.lineNumber() + ")";
                switch (consoleMessage.messageLevel()) {
                    case ERROR:
                        Log.e("PCKontrolJS", logText);
                        break;
                    case WARNING:
                        Log.w("PCKontrolJS", logText);
                        break;
                    default:
                        Log.d("PCKontrolJS", logText);
                        break;
                }
            }
            return true;
        }

        @Override
        public boolean onJsAlert(WebView view, String url, String message, final JsResult result) {
            if (activity.isFinishing() || activity.isDestroyed()) {
                result.cancel();
                return true;
            }
            new AlertDialog.Builder(activity)
                    .setTitle("PC Kontrol")
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                    .setOnCancelListener(d -> result.cancel())
                    .setCancelable(false)
                    .create()
                    .show();
            return true;
        }

        @Override
        public boolean onJsConfirm(WebView view, String url, String message, final JsResult result) {
            if (activity.isFinishing() || activity.isDestroyed()) {
                result.cancel();
                return true;
            }
            new AlertDialog.Builder(activity)
                    .setTitle("PC Kontrol")
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                    .setNegativeButton(android.R.string.cancel, (dialog, which) -> result.cancel())
                    .setOnCancelListener(d -> result.cancel())
                    .setCancelable(false)
                    .create()
                    .show();
            return true;
        }
    }

    private void handleLoadError(WebView view, String failingUrl, String errorMsg) {
        if (failingUrl != null && failingUrl.startsWith("file:///android_asset/")) {
            String safeErrorMsg = escapeHtml(errorMsg);
            String htmlData = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>"
                    + "<body style='background-color:#0A0A0B;color:#FFFFFF;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;padding:24px;box-sizing:border-box;text-align:center;'>"
                    + "<div style='background:#18181B;border:1px solid #27272A;border-radius:16px;padding:32px 24px;max-width:360px;width:100%;box-shadow:0 8px 24px rgba(0,0,0,0.5);'>"
                    + "<svg width='48' height='48' viewBox='0 0 24 24' fill='none' stroke='#EF4444' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' style='margin-bottom:16px;'><circle cx='12' cy='12' r='10'></circle><line x1='12' y1='8' x2='12' y2='12'></line><line x1='12' y1='16' x2='12.01' y2='16'></line></svg>"
                    + "<h2 style='font-size:18px;font-weight:600;margin:0 0 8px 0;color:#F4F4F5;'>Arayüz Yüklenemedi</h2>"
                    + "<p style='color:#A1A1AA;font-size:13px;line-height:1.5;margin:0 0 24px 0;'>" + safeErrorMsg + "</p>"
                    + "<button onclick='window.location.reload()' style='background:#3B82F6;color:#FFF;border:none;padding:12px 24px;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;width:100%;'>Yeniden Dene</button>"
                    + "</div></body></html>";
            view.loadDataWithBaseURL("file:///android_asset/", htmlData, "text/html", "UTF-8", null);
        }
    }

    private static String escapeHtml(String input) {
        if (input == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder(input.length());
        for (int i = 0; i < input.length(); i++) {
            char c = input.charAt(i);
            switch (c) {
                case '&':
                    sb.append("&amp;");
                    break;
                case '<':
                    sb.append("&lt;");
                    break;
                case '>':
                    sb.append("&gt;");
                    break;
                case '"':
                    sb.append("&quot;");
                    break;
                case '\'':
                    sb.append("&#39;");
                    break;
                default:
                    sb.append(c);
                    break;
            }
        }
        return sb.toString();
    }

    private void setupModernBackHandling() {
        if (Build.VERSION.SDK_INT >= 33) {
            try {
                Method getDispatcherMethod = Activity.class.getMethod("getOnBackInvokedDispatcher");
                Object dispatcher = getDispatcherMethod.invoke(this);
                if (dispatcher != null) {
                    Class<?> callbackClass = Class.forName("android.window.OnBackInvokedCallback");
                    Object callback = Proxy.newProxyInstance(
                            getClassLoader(),
                            new Class<?>[]{callbackClass},
                            (proxy, method, args) -> {
                                if ("onBackInvoked".equals(method.getName())) {
                                    handleBackPressed();
                                }
                                return null;
                            }
                    );
                    Method registerMethod = dispatcher.getClass().getMethod("registerOnBackInvokedCallback", int.class, callbackClass);
                    registerMethod.invoke(dispatcher, 0, callback);
                }
            } catch (Throwable t) {
                Log.d(TAG, "OnBackInvokedCallback reflection kaydı atlandı: " + t.getMessage());
            }
        }
    }

    private void handleBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }

        if (System.currentTimeMillis() - backPressedTime < 2000) {
            if (exitToast != null) {
                exitToast.cancel();
            }
            finish();
        } else {
            backPressedTime = System.currentTimeMillis();
            exitToast = Toast.makeText(this, "Çıkmak için tekrar basın", Toast.LENGTH_SHORT);
            exitToast.show();
        }
    }

    @Override
    public void onBackPressed() {
        handleBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) {
            webView.onPause();
            webView.pauseTimers();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
            webView.resumeTimers();
        }
    }

    @Override
    protected void onDestroy() {
        if (exitToast != null) {
            exitToast.cancel();
            exitToast = null;
        }
        if (webView != null) {
            webView.stopLoading();
            webView.removeJavascriptInterface("AndroidBridge");
        }
        if (androidBridge != null) {
            androidBridge.shutdown();
            androidBridge = null;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
