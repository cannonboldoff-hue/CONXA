# WF-19 retest: move/resize/minimize/restore ONLY Execute's own top-level window (found by process id; nothing else is touched, no input is sent).
# usage: wf19r_win.ps1 get | size <w> <h> | min | restore | front
param([string]$op, [int]$w = 0, [int]$h = 0)
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class W8{[StructLayout(LayoutKind.Sequential)]public struct R{public int l,t,r,b;}[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);[DllImport("user32.dll")]public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int cx,int cy,uint f);[DllImport("user32.dll")]public static extern bool IsIconic(IntPtr h);[DllImport("user32.dll")]public static extern bool IsZoomed(IntPtr h);[DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);}'
[W8]::SetProcessDPIAware() | Out-Null
$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -match 'conxa-execute' -and $_.CommandLine -notmatch '--type=' } | Select-Object -First 1
$h8 = (Get-Process -Id $p.ProcessId).MainWindowHandle
if ($op -eq 'min') { [W8]::ShowWindow($h8, 6) | Out-Null }
if ($op -eq 'restore') { [W8]::ShowWindow($h8, 9) | Out-Null }
if ($op -eq 'size') { [W8]::ShowWindow($h8, 9) | Out-Null; [W8]::SetWindowPos($h8, [IntPtr]::Zero, 100, 40, $w, $h, 0x14) | Out-Null }
if ($op -eq 'front') { [W8]::SetForegroundWindow($h8) | Out-Null }
$r = New-Object W8+R; [W8]::GetWindowRect($h8, [ref]$r) | Out-Null
"pid=$($p.ProcessId) rect=$($r.l),$($r.t),$($r.r),$($r.b) size=$($r.r-$r.l)x$($r.b-$r.t) iconic=$([W8]::IsIconic($h8)) zoomed=$([W8]::IsZoomed($h8))"
