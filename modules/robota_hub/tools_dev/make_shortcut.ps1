$ErrorActionPreference = 'Stop'
$root = 'D:\0_Robota'
$py = "$root\runtime\venv\Scripts\python.exe"

# 1) generate robota.ico from avatar.png
& $py -c @"
from PIL import Image
src = r'$root\robota\console\static\avatar.png'
im = Image.open(src)
w, h = im.size
sz = max(w, h)
sq = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
sq.paste(im, ((sz - w) // 2, (sz - h) // 2))
sq.save(r'$root\robota\console\static\robota.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('Updated robota.ico from avatar.png')
"@

# 2) CRLF for bat files
Get-ChildItem $root -Filter *.bat -Recurse -File | Where-Object { $_.FullName -notmatch '\\runtime\\' } | ForEach-Object {
  $t = [IO.File]::ReadAllText($_.FullName) -replace "`r?`n", "`r`n"
  [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding($false)))
}

# 3) Desktop shortcuts
$desk = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell

# Full Dashboard shortcut
$lnk1 = $ws.CreateShortcut("$desk\Robota.lnk")
$lnk1.TargetPath = "$root\runtime\venv\Scripts\pythonw.exe"
$lnk1.Arguments = "$root\robota\launch.py"
$lnk1.WorkingDirectory = $root
$lnk1.IconLocation = "$root\robota\console\static\robota.ico,0"
$lnk1.Description = "Robota AI Assistant"
$lnk1.Save()

# Mini Standalone Assistant Window shortcut
$lnk2 = $ws.CreateShortcut("$desk\Robota 桌面小窗.lnk")
$lnk2.TargetPath = "$root\Robota_Mini.bat"
$lnk2.WorkingDirectory = $root
$lnk2.IconLocation = "$root\robota\console\static\robota.ico,0"
$lnk2.Description = "Robota Mini AI Assistant"
$lnk2.Save()

Write-Host "Created shortcuts: Robota.lnk & Robota 桌面小窗.lnk"
