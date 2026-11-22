# WF-18 safe OS input. Usage: wf18_fg.ps1 <op> [args]
#  fg                      -> print foreground window pid/title/rect
#  click <procId> <x> <y>  -> click at physical px ONLY IF foreground window belongs to process <procId> (or its children) and (x,y) lies inside its rect
#  focus <procId>          -> try to bring the process main window to front (alt trick), print result
param([string]$op,[string]$a,[string]$b,[string]$c)
Add-Type -TypeDefinition @'
using System;using System.Text;using System.Runtime.InteropServices;
public class F { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
[StructLayout(LayoutKind.Sequential)] public struct R{public int l,t,r,b;}
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out R r);
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
[DllImport("user32.dll")] public static extern void mouse_event(int f,int x,int y,int d,int e);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
[DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,int f,int e); }
'@
[F]::SetProcessDPIAware() | Out-Null
function Fg { $h=[F]::GetForegroundWindow(); $p=0; [F]::GetWindowThreadProcessId($h,[ref]$p)|Out-Null; $s=New-Object Text.StringBuilder 256; [F]::GetWindowText($h,$s,256)|Out-Null; $r=New-Object F+R; [F]::GetWindowRect($h,[ref]$r)|Out-Null; [pscustomobject]@{h=$h;pid=$p;title=$s.ToString();l=$r.l;t=$r.t;r=$r.r;b=$r.b} }
switch ($op) {
 'fg' { $f=Fg; "pid=$($f.pid) title='$($f.title)' rect=$($f.l),$($f.t),$($f.r),$($f.b)" }
 'focus' { $p=Get-Process -Id ([int]$a); $h=$p.MainWindowHandle; [F]::ShowWindow($h,9)|Out-Null; [F]::keybd_event(0x12,0,0,0); [F]::SetForegroundWindow($h)|Out-Null; [F]::keybd_event(0x12,0,2,0); Start-Sleep -Milliseconds 400; $f=Fg; "pid=$($f.pid) title='$($f.title)'" }
 'keys' { $f=Fg; if ($f.pid -ne [int]$a) { "REFUSED: foreground pid $($f.pid) '$($f.title)' != $a"; exit 3 }; Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait($b); "keys sent to '$($f.title)'" }
 'click' { $f=Fg; $x=[int]$b; $y=[int]$c
   if ($f.pid -ne [int]$a) { "REFUSED: foreground pid $($f.pid) '$($f.title)' != $a"; exit 3 }
   if ($x -lt $f.l -or $x -gt $f.r -or $y -lt $f.t -or $y -gt $f.b) { "REFUSED: ($x,$y) outside window"; exit 4 }
   [F]::SetCursorPos($x,$y)|Out-Null; Start-Sleep -Milliseconds 150; [F]::mouse_event(2,0,0,0,0); Start-Sleep -Milliseconds 80; [F]::mouse_event(4,0,0,0,0); "clicked $x,$y on '$($f.title)'" }
}
