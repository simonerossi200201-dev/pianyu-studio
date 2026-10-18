"""Windows toast notification with no extra packages (PowerShell + WinRT), balloon-tip fallback.

Title/message are passed through environment variables and the script through
-EncodedCommand, so no user text is ever spliced into PowerShell source.
"""
from __future__ import annotations

import base64
import os
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:ROBOTA_N_TITLE)) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode($env:ROBOTA_N_MSG)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:ROBOTA_N_APP).Show($toast)
"""

_BALLOON = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = [System.Drawing.SystemIcons]::Information
$ni.BalloonTipTitle = $env:ROBOTA_N_TITLE
$ni.BalloonTipText = $env:ROBOTA_N_MSG
$ni.Visible = $true
$ni.ShowBalloonTip(8000)
Start-Sleep -Seconds 9
$ni.Dispose()
"""


def _encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _env(title: str, message: str) -> dict:
    env = dict(os.environ)
    env["ROBOTA_N_TITLE"] = (title or "Robota")[:200]
    env["ROBOTA_N_MSG"] = (message or "")[:1000]
    env["ROBOTA_N_APP"] = _APP_ID
    return env


def notify(title: str, message: str) -> dict:
    """Show a Windows notification. Returns within ~10 s at most."""
    env = _env(title, message)
    base = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand"]
    try:
        r = subprocess.run(base + [_encoded(_TOAST)], env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10, creationflags=_NO_WINDOW)
        if r.returncode == 0:
            return {"ok": True, "method": "toast"}
        err = (r.stderr or r.stdout).strip()[-300:]
    except subprocess.TimeoutExpired:
        err = "toast timed out"
    # fallback: balloon tip, detached so we don't block for its lifetime
    try:
        subprocess.Popen(base + [_encoded(_BALLOON)], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return {"ok": True, "method": "balloon", "toast_error": err}
    except OSError as exc:
        return {"ok": False, "error": f"toast failed ({err}); balloon failed ({exc})"}
