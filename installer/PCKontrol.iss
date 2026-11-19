; ============================================================================
; PC Kontrol - Inno Setup 6 Kurulum Betiği
; ============================================================================

[Setup]
AppId={{8F6C4E2B-5A91-4D3F-B8C2-9E1A7D6C3F01}
AppName=PC Kontrol
AppVersion=2.0.0
AppPublisher=PC Kontrol
DefaultDirName={autopf}\PC Kontrol
DefaultGroupName=PC Kontrol
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=PC-Kontrol-Kurulum
SetupIconFile=pc_kontrol.ico
UninstallDisplayIcon={app}\pc_kontrol.ico
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "install_pawnio"; Description: "CPU sıcaklık sensörü sürücüsünü (PawnIO) kur (Önerilen)"
Name: "configure_wol"; Description: "Wake-on-LAN ve güç ayarlarını otomatik yapılandır (Önerilen)"
Name: "desktopicon"; Description: "Masaüstüne 'PC Kontrol Paneli' kısayolu ekle"; Flags: unchecked

[Files]
Source: "..\build_dist\PCKontrolService\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "setup_network_wol.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "pc_kontrol.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "redist\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Tasks: install_pawnio; Check: not IsPawnIOInstalled

[Icons]
Name: "{group}\PC Kontrol Paneli"; Filename: "http://localhost:8765"; IconFilename: "{app}\pc_kontrol.ico"
Name: "{group}\PC Kontrol Kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PC Kontrol Paneli"; Filename: "http://localhost:8765"; IconFilename: "{app}\pc_kontrol.ico"; Tasks: desktopicon

[Run]
; 1. Windows Güvenlik Duvarı Kuralları (TCP 8765, UDP 7,9, UDP 8765 - Gelen ve Giden)
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol Sunucusu (TCP 8765)"""; Flags: runhidden; StatusMsg: "Windows Güvenlik Duvarı kuralları yapılandırılıyor..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol Sunucusu (TCP 8765)"" dir=in action=allow protocol=TCP localport=8765 profile=any description=""PC Kontrol Yerel Web ve API Sunucusu"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol Sunucusu Cikis (TCP 8765)"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol Sunucusu Cikis (TCP 8765)"" dir=out action=allow protocol=TCP localport=8765 profile=any description=""PC Kontrol Yerel Web ve API Sunucusu"""; Flags: runhidden

Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol WoL (UDP 7,9)"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol WoL (UDP 7,9)"" dir=in action=allow protocol=UDP localport=7,9 profile=any description=""PC Kontrol Wake-on-LAN Sihirli Paketi"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol WoL Cikis (UDP 7,9)"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol WoL Cikis (UDP 7,9)"" dir=out action=allow protocol=UDP localport=7,9 profile=any description=""PC Kontrol Wake-on-LAN Sihirli Paketi"""; Flags: runhidden

Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol Kesif (UDP 8765)"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol Kesif (UDP 8765)"" dir=in action=allow protocol=UDP localport=8765 profile=any description=""PC Kontrol UDP Broadcast Otomatik Kesif"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Kontrol Kesif Cikis (UDP 8765)"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Kontrol Kesif Cikis (UDP 8765)"" dir=out action=allow protocol=UDP localport=8765 profile=any description=""PC Kontrol UDP Broadcast Otomatik Kesif"""; Flags: runhidden

; 2. Eski Başlangıç Kalıntılarını Temizle ve (Seçildiyse) Wake-on-LAN Ayarla
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_network_wol.ps1"" -ConfigureWol"; Tasks: configure_wol; Flags: runhidden; StatusMsg: "Wake-on-LAN ve güç ayarları yapılandırılıyor..."
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_network_wol.ps1"" -CleanupOnly"; Tasks: not configure_wol; Flags: runhidden; StatusMsg: "Eski başlangıç kayıtları temizleniyor..."

; 3. CPU Sıcaklık Sensörü Sürücüsü (PawnIO)
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; Tasks: install_pawnio; Check: not IsPawnIOInstalled; StatusMsg: "CPU sıcaklık sensörü sürücüsü (PawnIO) kuruluyor..."

; 4. Windows Hizmeti Kaydı, Kurtarma Politikası ve Başlatma
Filename: "{sys}\sc.exe"; Parameters: "delete PCKontrolServisi"; Flags: runhidden; StatusMsg: "Windows Hizmeti yapılandırılıyor..."
Filename: "{sys}\sc.exe"; Parameters: "create PCKontrolServisi binPath= ""\""{app}\PCKontrolService.exe\"""" start= auto DisplayName= ""PC Kontrol Uzaktan Yönetim Servisi"""; Flags: runhidden; StatusMsg: "PC Kontrol Windows Hizmeti kaydediliyor..."
Filename: "{sys}\sc.exe"; Parameters: "description PCKontrolServisi ""Telefon ve yerel ağ tarayıcısı üzerinden PC donanım izleme ve güç yönetimi sağlar."""; Flags: runhidden
Filename: "{sys}\sc.exe"; Parameters: "failure PCKontrolServisi reset= 86400 actions= restart/5000/restart/15000/restart/30000"; Flags: runhidden
Filename: "{sys}\sc.exe"; Parameters: "start PCKontrolServisi"; Flags: runhidden; StatusMsg: "PC Kontrol Windows Hizmeti başlatılıyor..."

; 5. Kurulum Sonu Web Paneli Açılışı
Filename: "http://localhost:8765"; Description: "PC Kontrol Panelini tarayıcıda aç"; Flags: shellexec postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\PCKontrol"

[Code]
function IsPawnIOInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\PawnIO');
end;

procedure StopExistingServiceAndPort();
var
  ResultCode: Integer;
  SysDir: String;
  ScExe: String;
  TaskkillExe: String;
  PsCmd: String;
begin
  SysDir := ExpandConstant('{sys}');
  ScExe := SysDir + '\sc.exe';
  TaskkillExe := SysDir + '\taskkill.exe';

  Exec(ScExe, 'stop PCKontrolServisi', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 0 then
    Sleep(1500)
  else
    Sleep(300);

  PsCmd := '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ' +
    'ForEach-Object { if ($_.OwningProcess -gt 0) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }; ' +
    'Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ' +
    'Where-Object { ($_.Name -match ''python|PCKontrolService'') -and ($_.CommandLine -imatch ''server\.py|PCKontrolService'') } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  Exec('powershell.exe', PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  Exec(TaskkillExe, '/F /IM PCKontrolService.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    StopExistingServiceAndPort();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  SysDir: String;
  ScExe: String;
  TaskkillExe: String;
  NetshExe: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    SysDir := ExpandConstant('{sys}');
    ScExe := SysDir + '\sc.exe';
    TaskkillExe := SysDir + '\taskkill.exe';
    NetshExe := SysDir + '\netsh.exe';

    Exec(ScExe, 'stop PCKontrolServisi', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
    Exec(TaskkillExe, '/F /IM PCKontrolService.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(400);
    Exec(ScExe, 'delete PCKontrolServisi', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol Sunucusu (TCP 8765)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol Sunucusu Cikis (TCP 8765)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol WoL (UDP 7,9)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol WoL Cikis (UDP 7,9)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol Kesif (UDP 8765)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(NetshExe, 'advfirewall firewall delete rule name="PC Kontrol Kesif Cikis (UDP 8765)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;