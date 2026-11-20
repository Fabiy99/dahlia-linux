# Dahlia USB Creator: writes the installer ISO to a USB stick and reads it
# back to compare. Started elevated by the app (lib/src/usb_creator.dart);
# reports progress as JSON in the file given by -Progress.
# -Bus is the bus type a disk must have; CI uses a virtual disk.
param([int]$Disk, [long]$Size, [string]$Iso, [string]$Progress, [string]$Bus = 'USB')
$ErrorActionPreference = 'Stop'
function Report([string]$State, [long]$Done, [long]$Total, [string]$Message) {
  $json = @{ state = $State; done = $Done; total = $Total; message = $Message } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText("$Progress.tmp", $json)
  Move-Item -Force "$Progress.tmp" $Progress
}
$src = $null
$dst = $null
try {
  $d = Get-Disk -Number $Disk
  if ([string]$d.BusType -ne $Bus) { throw "Disk $Disk is not a USB disk." }
  if ($d.Size -ne $Size) { throw "Disk $Disk has changed. Choose the stick again." }
  if ($d.IsBoot -or $d.IsSystem) { throw "Disk $Disk holds the running system." }
  Report 'preparing' 0 0 ''
  if ($d.IsOffline) { Set-Disk -Number $Disk -IsOffline $false }
  if ($d.IsReadOnly) { Set-Disk -Number $Disk -IsReadOnly $false }
  if ($d.PartitionStyle -ne 'RAW') { Clear-Disk -Number $Disk -RemoveData -RemoveOEM -Confirm:$false }
  # Offline, Windows mounts nothing while the new partition table appears; raw access still works.
  Set-Disk -Number $Disk -IsOffline $true
  $src = [IO.File]::OpenRead($Iso)
  $total = $src.Length
  if ($total -gt $Size) { throw 'The stick is too small.' }
  $dst = New-Object IO.FileStream("\\.\PhysicalDrive$Disk", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite, 4096, [IO.FileOptions]::WriteThrough)
  $buf = New-Object byte[] (4MB)
  $done = 0L
  $clock = [Diagnostics.Stopwatch]::StartNew()
  while (($n = $src.Read($buf, 0, $buf.Length)) -gt 0) {
    if ($n % 4096 -ne 0) { $pad = 4096 - ($n % 4096); [Array]::Clear($buf, $n, $pad); $n += $pad }
    $dst.Write($buf, 0, $n)
    $done += $n
    if ($clock.ElapsedMilliseconds -gt 500) { Report 'writing' ([Math]::Min($done, $total)) $total ''; $clock.Restart() }
  }
  $dst.Flush()
  # Read back what was written and compare it with the ISO.
  Report 'verifying' 0 $total ''
  $src.Position = 0
  $dst.Position = 0
  $a = [Security.Cryptography.SHA256]::Create()
  $b = [Security.Cryptography.SHA256]::Create()
  $buf2 = New-Object byte[] (4MB)
  $left = $total
  while ($left -gt 0) {
    $n = $src.Read($buf, 0, [Math]::Min($buf.Length, $left))
    $want = $n
    if ($want % 4096 -ne 0) { $want += 4096 - ($want % 4096) }
    $got = 0
    while ($got -lt $want) { $r = $dst.Read($buf2, $got, $want - $got); if ($r -le 0) { throw 'Reading the stick back failed.' }; $got += $r }
    [void]$a.TransformBlock($buf, 0, $n, $null, 0)
    [void]$b.TransformBlock($buf2, 0, $n, $null, 0)
    $left -= $n
    if ($clock.ElapsedMilliseconds -gt 500) { Report 'verifying' ($total - $left) $total ''; $clock.Restart() }
  }
  [void]$a.TransformFinalBlock($buf, 0, 0)
  [void]$b.TransformFinalBlock($buf2, 0, 0)
  if ([BitConverter]::ToString($a.Hash) -ne [BitConverter]::ToString($b.Hash)) { throw 'The stick does not match the ISO after writing.' }
  $dst.Close(); $dst = $null
  Set-Disk -Number $Disk -IsOffline $false
  Update-Disk -Number $Disk -ErrorAction SilentlyContinue
  Report 'done' $total $total ''
} catch {
  Report 'error' 0 0 $_.Exception.Message
  exit 1
} finally {
  if ($src) { $src.Close() }
  if ($dst) { $dst.Close() }
  Set-Disk -Number $Disk -IsOffline $false -ErrorAction SilentlyContinue
}
