# toast_relay.ps1 - S4U toast relay (2026-10-23)
# Reads the newest spool file in reports\_toast_spool\ and shows the WinRT toast
# in the USER session (Interactive task). S4U/session-0 callers cannot toast;
# notify.py writes a spool file and triggers this task via schtasks /run.
# Self-cleaning: consumes the spool file it showed; drops spool files older than 24h.
$ErrorActionPreference = 'Continue'
$spoolDir = 'D:\zcodesj\reports\_toast_spool'
$relayLog = 'D:\zcodesj\reports\toast_relay.log'
if (-not (Test-Path $spoolDir)) { exit 0 }

# cleanup stale spool (>24h)
Get-ChildItem $spoolDir -Filter '*.txt' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-24) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$newest = Get-ChildItem $spoolDir -Filter '*.txt' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $newest) { exit 0 }

$lines = Get-Content $newest.FullName -Encoding UTF8
$title = if ($lines.Count -gt 0) { $lines[0] } else { 'TulongSystem' }
$content = if ($lines.Count -gt 1) { $lines[1] } else { '' }

try {
    $ErrorActionPreference = 'Stop'
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $nodes = $t.GetElementsByTagName('text')
    $nodes.Item(0).AppendChild($t.CreateTextNode($title)) | Out-Null
    $nodes.Item(1).AppendChild($t.CreateTextNode($content)) | Out-Null
    $n = [Windows.UI.Notifications.ToastNotification]::new($t)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TulongSystem').Show($n)
    Remove-Item $newest.FullName -Force -ErrorAction SilentlyContinue
    Add-Content -Path $relayLog -Value ("{0}|RELAY-OK|{1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $title) -Encoding UTF8
} catch {
    Add-Content -Path $relayLog -Value ("{0}|RELAY-FAIL|{1}|{2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $title, $_.Exception.Message) -Encoding UTF8
}
