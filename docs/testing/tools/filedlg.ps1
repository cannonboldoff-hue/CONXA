# Drive the native "Open" dialog of ONE Studio instance (win32 messages, no keyboard/mouse).
# Usage: filedlg.ps1 <studioPid> <folderOrPath> ["name1|name2|..."]
#  with names: go to the folder, then pick those names (multi-select). Without names: open the path as typed.
param([int]$ProcId, [string]$Folder, [string]$NamesPipe)
Add-Type -AssemblyName UIAutomationClient
Add-Type -TypeDefinition @'
using System; using System.Text; using System.Runtime.InteropServices;
public class DW {
 public delegate bool EnumProc(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc e, IntPtr l);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, string l);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, IntPtr l);
 [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h, int id);
 public static IntPtr FindEdit(IntPtr dlg) { IntPtr r = IntPtr.Zero; EnumChildWindows(dlg, (h,l)=>{ var sb=new StringBuilder(64); GetClassName(h,sb,64); if(sb.ToString()=="Edit"){ IntPtr par=IntPtr.Zero; var p=new StringBuilder(64); /* first Edit under ComboBoxEx32 */ r=h; return false;} return true; }, IntPtr.Zero); return r; }
}
'@
$UI = [Windows.Automation.AutomationElement]; $root = $UI::RootElement
function Find-Dlg { foreach ($w in $root.FindAll('Children',[Windows.Automation.Condition]::TrueCondition)) { if ($w.Current.ProcessId -eq $ProcId) { foreach ($c in $w.FindAll('Children',[Windows.Automation.Condition]::TrueCondition)) { if ($c.Current.ClassName -eq '#32770') { return $c } } } } }
function Fill($text) {
  $d = Find-Dlg; $h = [IntPtr]$d.Current.NativeWindowHandle
  $e = [DW]::GetDlgItem($h, 0x47C)  # ComboBoxEx32 "File name"
  $edit = [DW]::FindEdit($e)
  [DW]::SendMessage($edit, 0x000C, [IntPtr]::Zero, $text) | Out-Null   # WM_SETTEXT
  [DW]::SendMessage([DW]::GetDlgItem($h, 1), 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null  # BM_CLICK on Open
}
$dlg = $null; for ($i=0; $i -lt 30 -and -not $dlg; $i++) { $dlg = Find-Dlg; if (-not $dlg) { Start-Sleep -Milliseconds 500 } }
if (-not $dlg) { "NO DIALOG"; exit 1 }
$Names = if ($NamesPipe) { $NamesPipe -split '\|' } else { @() }
if ($Folder -eq "CANCEL") { $d=Find-Dlg; [DW]::SendMessage([DW]::GetDlgItem([IntPtr]$d.Current.NativeWindowHandle, 2), 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null; "cancelled"; exit 0 }
if ($Names.Count) { Fill $Folder; Start-Sleep -Milliseconds 1000; Fill (($Names | % { '"' + $_ + '"' }) -join ' ') } else { Fill $Folder }
"done"
