# CI: runs assets/usb-writer.ps1 against an attached virtual disk (admin).
# Checks a full write with read-back, and that a changed size and the
# default bus type (USB) are refused.
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:RUNNER_TEMP 'usb-writer'
New-Item -ItemType Directory -Force $dir | Out-Null
$vhd = Join-Path $dir 'stick.vhdx'
$writer = Join-Path $PSScriptRoot '..\..\assets\usb-writer.ps1'
"create vdisk file=`"$vhd`" maximum=64 type=expandable`r`nattach vdisk" | Set-Content -Encoding ascii "$dir\attach.txt"
"select vdisk file=`"$vhd`"`r`ndetach vdisk" | Set-Content -Encoding ascii "$dir\detach.txt"
diskpart /s "$dir\attach.txt" | Out-Host
try {
  Start-Sleep -Seconds 2
  $disk = Get-Disk | Where-Object Location -eq $vhd
  if (-not $disk) { throw 'virtual disk not found' }
  $iso = Join-Path $dir 'test.iso'
  $bytes = New-Object byte[] (10MB + 123)
  (New-Object Random 42).NextBytes($bytes)
  [IO.File]::WriteAllBytes($iso, $bytes)
  $bus = [string]$disk.BusType

  function Run([long]$Size, [string[]]$Extra) {
    $progress = Join-Path $dir ("progress-{0}.json" -f [guid]::NewGuid())
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $writer -Disk $disk.Number -Size $Size -Iso $iso -Progress $progress @Extra | Out-Host
    return Get-Content $progress -Raw | ConvertFrom-Json
  }

  $p = Run $disk.Size @('-Bus', $bus)
  if ($p.state -ne 'done' -or $p.total -ne $bytes.Length) { throw "write: $($p.state) $($p.message)" }
  # The first bytes on the disk are the ISO's.
  $s = New-Object IO.FileStream("\\.\PhysicalDrive$($disk.Number)", [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
  $head = New-Object byte[] 4096
  [void]$s.Read($head, 0, 4096)
  $s.Close()
  for ($i = 0; $i -lt 4096; $i++) { if ($head[$i] -ne $bytes[$i]) { throw "byte $i differs" } }

  $p = Run ($disk.Size + 1) @('-Bus', $bus)
  if ($p.state -ne 'error' -or $p.message -notmatch 'changed') { throw "size check: $($p.state) $($p.message)" }
  $p = Run $disk.Size @()
  if ($p.state -ne 'error' -or $p.message -notmatch 'not a USB disk') { throw "bus check: $($p.state) $($p.message)" }
  Write-Host 'usb-writer: ok'
} finally {
  diskpart /s "$dir\detach.txt" | Out-Host
}
