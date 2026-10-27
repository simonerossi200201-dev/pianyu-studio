# 片语工作室 · 卡死诊断信息收集（只读，不修改任何设置）
# 收集：软件日志、队列/设置文件、Windows 系统事件（显卡驱动重置 / 意外断电 / 硬件错误 / 程序无响应）、硬件信息
# 结果：桌面上的 片语工作室_卡死诊断_<时间>.zip
$ErrorActionPreference = "SilentlyContinue"
$ts   = Get-Date -Format "yyyyMMdd_HHmmss"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$out  = Join-Path $env:TEMP "pianyu_diag_$ts"
New-Item -ItemType Directory -Path $out -Force | Out-Null

Write-Host "[1/5] 复制软件日志..."
$userData = Join-Path $env:LOCALAPPDATA "CUTORA"
$portable = Join-Path $root "modules\cutora_editor\UserData"
foreach ($ud in @($userData, $portable)) {
    if (Test-Path $ud) {
        $dst = Join-Path $out ("UserData_" + (Split-Path $ud -Leaf))
        New-Item -ItemType Directory -Path $dst -Force | Out-Null
        Copy-Item (Join-Path $ud "logs") $dst -Recurse -Force
        Copy-Item (Join-Path $ud "settings") $dst -Recurse -Force
        Copy-Item (Join-Path $ud "jobs") $dst -Recurse -Force
        Copy-Item (Join-Path $ud "export_state.json") $dst -Force
    }
}
foreach ($p in @("data\logs", "data\install_health_report.json", "logs")) {
    $src = Join-Path $root $p
    if (Test-Path $src) { Copy-Item $src (Join-Path $out ("studio_" + ($p -replace '[\\/]', '_'))) -Recurse -Force }
}

Write-Host "[2/5] 读取最近 7 天的 Windows 系统事件..."
$since = (Get-Date).AddDays(-7)
$sys = Get-WinEvent -FilterHashtable @{ LogName = "System"; StartTime = $since } -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProviderName -match "nvlddmkm|Display|Kernel-Power|WHEA|EventLog|disk|Ntfs|Resource-Exhaustion|volmgr|Kernel-Boot|BugCheck" -or
        $_.Id -in 41, 1001, 4101, 6008, 2004, 153, 129
    }
$sys | Select-Object TimeCreated, Id, ProviderName, LevelDisplayName, Message |
    Format-List | Out-File (Join-Path $out "windows_system_events.txt") -Encoding UTF8
$app = Get-WinEvent -FilterHashtable @{ LogName = "Application"; StartTime = $since } -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -match "Application Error|Application Hang|Windows Error Reporting" -and $_.Message -match "python|pythonw|ffmpeg|cutora|nvenc|nv" }
$app | Select-Object TimeCreated, Id, ProviderName, Message |
    Format-List | Out-File (Join-Path $out "windows_app_crashes.txt") -Encoding UTF8

Write-Host "[3/5] 硬件与驱动信息..."
$hw = @()
$hw += "电脑名: $env:COMPUTERNAME"
$os = Get-CimInstance Win32_OperatingSystem
$hw += "系统: $($os.Caption) $($os.Version) build $($os.BuildNumber)"
$hw += "上次开机: $($os.LastBootUpTime)"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$hw += "CPU: $($cpu.Name)  核心 $($cpu.NumberOfCores) / 线程 $($cpu.NumberOfLogicalProcessors)"
$hw += "内存: {0:N1} GB 总计 / {1:N1} GB 当前可用" -f ($os.TotalVisibleMemorySize / 1MB), ($os.FreePhysicalMemory / 1MB)
foreach ($g in Get-CimInstance Win32_VideoController) { $hw += "显卡: $($g.Name)  驱动 $($g.DriverVersion)  日期 $($g.DriverDate)" }
foreach ($d in Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3") { $hw += "磁盘 $($d.DeviceID) 剩余 {0:N1} GB / {1:N1} GB" -f ($d.FreeSpace / 1GB), ($d.Size / 1GB) }
foreach ($pd in Get-PhysicalDisk) { $hw += "物理盘: $($pd.FriendlyName)  类型 $($pd.MediaType)  健康 $($pd.HealthStatus)" }
foreach ($pf in Get-CimInstance Win32_PageFileUsage) { $hw += "页面文件: $($pf.Name)  大小 $($pf.AllocatedBaseSize) MB  峰值 $($pf.PeakUsage) MB" }
$hw | Out-File (Join-Path $out "hardware.txt") -Encoding UTF8
& nvidia-smi 2>&1 | Out-File (Join-Path $out "nvidia_smi.txt") -Encoding UTF8
& nvidia-smi -q -d PERFORMANCE,TEMPERATURE,POWER,CLOCK 2>&1 | Out-File (Join-Path $out "nvidia_smi_detail.txt") -Encoding UTF8

Write-Host "[4/5] 软件版本..."
Push-Location $root
& git log -1 --oneline 2>&1 | Out-File (Join-Path $out "version.txt") -Encoding UTF8
Pop-Location
Get-ChildItem (Join-Path $root "modules\cutora_editor\cutora\core\cutora_rust_core.dll") |
    Select-Object FullName, Length, LastWriteTime | Out-File (Join-Path $out "version.txt") -Append -Encoding UTF8

Write-Host "[5/5] 打包到桌面..."
$zip = Join-Path ([Environment]::GetFolderPath("Desktop")) "片语工作室_卡死诊断_$ts.zip"
Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip -Force
Remove-Item $out -Recurse -Force
Write-Host ""
Write-Host "完成！请把桌面上的这个文件发给负责人：" -ForegroundColor Green
Write-Host "  $zip" -ForegroundColor Green
