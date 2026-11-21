# Print Studio window rect, set pos: winrect.ps1 [x y w h]  (physical px)
param($x,$y,$w,$h)
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class WR{[StructLayout(LayoutKind.Sequential)]public struct R{public int l,t,r,b;}[DllImport("user32.dll")]public static extern bool GetWindowRect(IntPtr h,out R r);[DllImport("user32.dll")]public static extern bool MoveWindow(IntPtr h,int x,int y,int w,int ht,bool rp);[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();}'
[WR]::SetProcessDPIAware()|Out-Null
$p=Get-Process electron | ? MainWindowTitle -eq 'Conxa Build Studio' | select -First 1
$r=New-Object WR+R; [WR]::GetWindowRect($p.MainWindowHandle,[ref]$r)|Out-Null
if($w){[WR]::MoveWindow($p.MainWindowHandle,[int]$x,[int]$y,[int]$w,[int]$h,$true)|Out-Null; [WR]::GetWindowRect($p.MainWindowHandle,[ref]$r)|Out-Null}
"pid=$($p.Id) rect=$($r.l),$($r.t),$($r.r),$($r.b)"
