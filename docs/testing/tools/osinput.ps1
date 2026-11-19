# OS-level (trusted) input. Usage: osinput.ps1 click <physX> <physY> | move <x> <y> | type <text> | key <sendkeys>
# Coordinates are PHYSICAL screen pixels (process is DPI aware). Studio window must be foreground.
param([string]$op,[string]$a,[string]$b)
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h,int c);
[DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,int f,int e);
[DllImport("user32.dll")] public static extern void mouse_event(int f,int x,int y,int d,int e); }
'@
[W]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms
switch ($op) {
 'focus' { $h=(Get-Process electron | ? MainWindowTitle -eq 'Conxa Build Studio' | select -First 1).MainWindowHandle; [W]::ShowWindow($h,9)|Out-Null; [W]::keybd_event(0x12,0,0,0); [W]::SetForegroundWindow($h)|Out-Null; [W]::keybd_event(0x12,0,2,0) }
 'move' { [W]::SetCursorPos([int]$a,[int]$b) | Out-Null }
 'click' { [W]::SetCursorPos([int]$a,[int]$b) | Out-Null; Start-Sleep -Milliseconds 150; [W]::mouse_event(2,0,0,0,0); Start-Sleep -Milliseconds 80; [W]::mouse_event(4,0,0,0,0) }
 'type' { [System.Windows.Forms.SendKeys]::SendWait(($a -replace '([+^%~(){}\[\]])','{$1}')) }
 'key' { [System.Windows.Forms.SendKeys]::SendWait($a) }
}
