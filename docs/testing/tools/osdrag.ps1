# OS-level drag (physical px): osdrag.ps1 x1 y1 x2 y2
param([int]$x1,[int]$y1,[int]$x2,[int]$y2)
Add-Type -TypeDefinition 'using System.Runtime.InteropServices;public class DD{[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();[DllImport("user32.dll")]public static extern bool SetCursorPos(int x,int y);[DllImport("user32.dll")]public static extern void mouse_event(int f,int x,int y,int d,int e);}'
[DD]::SetProcessDPIAware()|Out-Null
[DD]::SetCursorPos($x1,$y1)|Out-Null; Start-Sleep -Milliseconds 200; [DD]::mouse_event(2,0,0,0,0)
for($i=1;$i -le 12;$i++){ [DD]::SetCursorPos([int]($x1+($x2-$x1)*$i/12),[int]($y1+($y2-$y1)*$i/12))|Out-Null; Start-Sleep -Milliseconds 40 }
Start-Sleep -Milliseconds 150; [DD]::mouse_event(4,0,0,0,0)
