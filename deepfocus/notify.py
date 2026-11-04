"""Windows toast notifications via the WinRT API exposed to PowerShell.
Fails silently: a notification is never worth interrupting the app for."""
from __future__ import annotations

import base64
import os
import subprocess
import threading
from pathlib import Path
from xml.sax.saxutils import escape

CREATE_NO_WINDOW = 0x08000000

# The toast XML sits in a single-quoted here-string (@'...'@): PowerShell expands nothing inside one, so text that
# contains $variables or $(expressions) is shown as-is instead of being evaluated. The script itself travels as
# -EncodedCommand, so no part of it is parsed as a command line either.
_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = @'
<toast><visual><binding template="ToastGeneric"><text>{title}</text><text>{body}</text></binding></visual></toast>
'@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{appid}").Show($toast)
"""

APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _powershell() -> str:
    """Absolute path, so nothing is resolved through PATH or the current folder."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
               / "powershell.exe")


def _text(s: str) -> str:
    """One line of XML-escaped text (a newline could otherwise end the here-string early)."""
    return escape(" ".join(str(s).split()))


def script_for(title: str, body: str) -> str:
    return _PS.replace("{title}", _text(title)).replace("{body}", _text(body)).replace("{appid}", APP_ID)


def toast(title: str, body: str) -> None:
    encoded = base64.b64encode(script_for(title, body).encode("utf-16-le")).decode("ascii")

    def _go():
        try:
            subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                            "-EncodedCommand", encoded],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()
