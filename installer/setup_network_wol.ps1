param(
    [switch]$CleanupOnly,
    [switch]$ConfigureWol
)

$ErrorActionPreference = 'SilentlyContinue'

function Remove-LegacyStartupArtifacts {
    # 1. Eski Görev Zamanlayıcı (Task Scheduler) kaydını kaldır
    try {
        $task = Get-ScheduledTask -TaskName 'PC_Kontrol_Servisi' -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            Unregister-ScheduledTask -TaskName 'PC_Kontrol_Servisi' -Confirm:$false -ErrorAction SilentlyContinue
        }
    } catch {}
    try {
        & schtasks.exe /Delete /TN "PC_Kontrol_Servisi" /F 2>$null | Out-Null
    } catch {}

    # 2. Başlangıç klasörlerindeki eski PC_Kontrol_Servisi.vbs dosyalarını temizle
    $startupDirs = New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($special in @('Startup', 'CommonStartup')) {
        try {
            $dir = [Environment]::GetFolderPath($special)
            if (-not [string]::IsNullOrWhiteSpace($dir)) {
                [void]$startupDirs.Add($dir)
            }
        } catch {}
    }

    try {
        $profileDirs = Get-ChildItem -Path (Join-Path $env:SystemDrive 'Users') -Directory -ErrorAction SilentlyContinue
        foreach ($prof in $profileDirs) {
            $candidate = Join-Path $prof.FullName 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
            [void]$startupDirs.Add($candidate)
        }
    } catch {}

    try {
        $profileKeys = Get-ChildItem -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' -ErrorAction SilentlyContinue
        foreach ($key in $profileKeys) {
            $imgPath = (Get-ItemProperty -Path $key.PSPath -Name 'ProfileImagePath' -ErrorAction SilentlyContinue).ProfileImagePath
            if (-not [string]::IsNullOrWhiteSpace($imgPath)) {
                $candidate = Join-Path $imgPath 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
                [void]$startupDirs.Add($candidate)
            }
        }
    } catch {}

    foreach ($dir in $startupDirs) {
        try {
            $vbsFile = Join-Path $dir 'PC_Kontrol_Servisi.vbs'
            if (Test-Path -LiteralPath $vbsFile) {
                Remove-Item -LiteralPath $vbsFile -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }

    # 3. Kayıt Defteri (HKCU & yüklü HKEY_USERS profilleri) Run anahtarını temizle
    $runPaths = New-Object System.Collections.Generic.List[string]
    $runPaths.Add('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run')
    try {
        $userHives = Get-ChildItem -Path 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue
        foreach ($hive in $userHives) {
            if ($hive.PSChildName -notlike '*_Classes') {
                $runPaths.Add(($hive.PSPath + '\Software\Microsoft\Windows\CurrentVersion\Run'))
            }
        }
    } catch {}

    foreach ($runPath in $runPaths) {
        try {
            if (Test-Path -Path $runPath) {
                $prop = Get-ItemProperty -Path $runPath -Name 'PC_Kontrol_Servisi' -ErrorAction SilentlyContinue
                if ($null -ne $prop) {
                    Remove-ItemProperty -Path $runPath -Name 'PC_Kontrol_Servisi' -ErrorAction SilentlyContinue
                }
            }
        } catch {}
    }
}

function Set-WakeOnLanConfiguration {
    # 1. Windows Hızlı Başlatmayı (Fast Startup) devre dışı bırak (S5 WoL için gerekli)
    try {
        $powerKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
        if (-not (Test-Path -Path $powerKey)) {
            New-Item -Path $powerKey -Force -ErrorAction SilentlyContinue | Out-Null
        }
        Set-ItemProperty -Path $powerKey -Name 'HiberbootEnabled' -Value 0 -Type DWord -Force -ErrorAction Stop
    } catch {}

    # 2. Fiziksel ağ kartlarının Wake-on-LAN ve güç yönetimi özelliklerini yapılandır
    try {
        $adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Disabled' }
        foreach ($adapter in $adapters) {
            try {
                Enable-NetAdapterPowerManagement -Name $adapter.Name -ErrorAction SilentlyContinue | Out-Null
            } catch {}

            try {
                Set-NetAdapterPowerManagement -Name $adapter.Name -WakeOnMagicPacket Enabled -WakeOnPattern Enabled -ErrorAction SilentlyContinue | Out-Null
            } catch {}

            try {
                $advProps = Get-NetAdapterAdvancedProperty -Name $adapter.Name -ErrorAction SilentlyContinue
                foreach ($prop in $advProps) {
                    $dn = [string]$prop.DisplayName
                    if ($dn -like '*Magic Packet*' -or $dn -like '*Wake on*' -or $dn -like '*Shutdown Wake*' -or $dn -like '*S5*' -or $dn -like '*PME*') {
                        try {
                            Set-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $dn -DisplayValue 'Enabled' -ErrorAction Stop | Out-Null
                        } catch {
                            try {
                                Set-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $dn -DisplayValue '1' -ErrorAction SilentlyContinue | Out-Null
                            } catch {}
                        }
                    }
                    if ($dn -like '*Energy Efficient*' -or $dn -like '*Green Ethernet*' -or $dn -like '*Power Saving*') {
                        try {
                            Set-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $dn -DisplayValue 'Disabled' -ErrorAction Stop | Out-Null
                        } catch {
                            try {
                                Set-NetAdapterAdvancedProperty -Name $adapter.Name -DisplayName $dn -DisplayValue '0' -ErrorAction SilentlyContinue | Out-Null
                            } catch {}
                        }
                    }
                }
            } catch {}
        }
    } catch {}

    # 3. powercfg üzerinden ağ cihazlarına uyandırma yetkisi ver
    try {
        $wakeDevices = & powercfg.exe /devicequery wake_from_any 2>$null
        if ($wakeDevices) {
            foreach ($line in ($wakeDevices -split "`r?`n")) {
                $trimmed = $line.Trim()
                if ($trimmed -and ($trimmed -match 'Ethernet|LAN|Realtek|Intel|Killer|Broadcom|Network')) {
                    try {
                        & powercfg.exe /deviceenablewake "$trimmed" 2>$null | Out-Null
                    } catch {}
                }
            }
        }
    } catch {}
}

Remove-LegacyStartupArtifacts

if ($ConfigureWol -and -not $CleanupOnly) {
    Set-WakeOnLanConfiguration
}

exit 0