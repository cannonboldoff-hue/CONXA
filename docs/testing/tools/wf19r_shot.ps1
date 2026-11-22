# WF-19 retest: screen copy of Execute's window rectangle, ONLY when Execute is the foreground window (otherwise nothing is saved: the owner's other windows may be on top).
# usage: wf19r_shot.ps1 <out.png>   (brings Execute's own window to the front first; no input is sent to anything)
param([string]$out)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class W7{[StructLayout(LayoutKind.Sequential)]public struct R{public int l,t,r,b;}[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();[DllImport("user32.dll")]public static extern IntPtr GetForegroundWindow();[DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);[DllImport("user32.dll")]public static extern bool IsIconic(IntPtr h);[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);}'
[W7]::SetProcessDPIAware() | Out-Null
$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -match 'conxa-execute' -and $_.CommandLine -notmatch '--type=' } | Select-Object -First 1
$h = (Get-Process -Id $p.ProcessId).MainWindowHandle
if ([W7]::IsIconic($h)) { "NOT SAVED: window is minimized"; return }
[W7]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 700
if ([W7]::GetForegroundWindow() -ne $h) { "NOT SAVED: Execute is not the foreground window"; return }
$r = New-Object W7+R; [W7]::GetWindowRect($h, [ref]$r) | Out-Null; $w = $r.r - $r.l; $ht = $r.b - $r.t
$bmp = New-Object System.Drawing.Bitmap $w, $ht; $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.l, $r.t, 0, 0, (New-Object System.Drawing.Size $w, $ht)); $bmp.Save($out)
"saved $out ${w}x${ht}"
