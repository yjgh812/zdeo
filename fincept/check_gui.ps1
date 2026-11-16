Get-Process python -ErrorAction SilentlyContinue |
  Sort-Object WorkingSet64 -Descending |
  Select-Object -First 5 Id,
    @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB)}},
    @{n='Threads';e={$_.Threads.Count}},
    @{n='WinTitle';e={$_.MainWindowTitle}} |
  Format-Table -AutoSize
