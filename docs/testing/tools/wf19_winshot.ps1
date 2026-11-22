# WF-19: screenshot of the Conxa Execute window area only (screen copy of its rectangle, so native panel views show). usage: wf19_winshot.ps1 <out.png> [-Front]
param([string]$out, [switch]$Front)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class W9{[StructLayout(LayoutKind.Sequential)]public struct R{public int l,t,r,b;}[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();[DllImport("user32.dll")]public static extern IntPtr GetForegroundWindow();[DllImport("user32.dll")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);[DllImport("user32.dll")]public static extern bool IsIconic(IntPtr h);}'
[W9]::SetProcessDPIAware() | Out-Null
$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -match 'conxa-execute' -and $_.CommandLine -notmatch '--type=' } | Select-Object -First 1
$gp = Get-Process -Id $p.ProcessId; $h = $gp.MainWindowHandle
$r = New-Object W9+R; [W9]::GetWindowRect($h, [ref]$r) | Out-Null
$fp = 0; [W9]::GetWindowThreadProcessId([W9]::GetForegroundWindow(), [ref]$fp) | Out-Null
$w = $r.r - $r.l; $ht = $r.b - $r.t
$bmp = New-Object System.Drawing.Bitmap $w, $ht; $g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.l, $r.t, 0, 0, (New-Object System.Drawing.Size $w, $ht)); $bmp.Save($out)
"rect=$($r.l),$($r.t),$($r.r),$($r.b) iconic=$([W9]::IsIconic($h)) foreground_pid=$fp execute_pid=$($p.ProcessId) title='$($gp.MainWindowTitle)'"
