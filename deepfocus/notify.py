"""Windows toast notifications via the WinRT API exposed to PowerShell.
Fails silently: a notification is never worth interrupting the app for."""
from __future__ import annotations

import subprocess
import threading
from xml.sax.saxutils import escape

CREATE_NO_WINDOW = 0x08000000

_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @"
<toast><visual><binding template="ToastGeneric"><text>{title}</text><text>{body}</text></binding></visual></toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{appid}").Show($toast)
"""

APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def toast(title: str, body: str) -> None:
    script = _PS.replace("{title}", escape(title)).replace("{body}", escape(body)).replace("{appid}", APP_ID)

    def _go():
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()
