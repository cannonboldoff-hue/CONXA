# Move/resize one window WITHOUT activating or changing z-order. Usage: winpos.ps1 <hwnd> <x> <y> <w> <h>   (hwnd 0 = just print rect)
param([long]$H,[int]$X,[int]$Y,[int]$W,[int]$Ht)
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class WP { [StructLayout(LayoutKind.Sequential)] public struct R{public int l,t,r,b;} [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x,int y,int w,int ht,uint f); [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r); }'
[WP]::SetProcessDPIAware() | Out-Null
$r = New-Object WP+R; [WP]::GetWindowRect([IntPtr]$H,[ref]$r) | Out-Null; "before $($r.l),$($r.t),$($r.r),$($r.b)"
if ($W) { [WP]::SetWindowPos([IntPtr]$H,[IntPtr]::Zero,$X,$Y,$W,$Ht,0x0214) | Out-Null; [WP]::GetWindowRect([IntPtr]$H,[ref]$r) | Out-Null; "after $($r.l),$($r.t),$($r.r),$($r.b)" }
