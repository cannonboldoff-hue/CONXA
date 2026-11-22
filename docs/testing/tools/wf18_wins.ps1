# list visible top-level windows (pid, title, rect) whose title matches pattern
param([string]$pat='Prove you are human')
Add-Type -TypeDefinition @'
using System;using System.Text;using System.Collections.Generic;using System.Runtime.InteropServices;
public class WL { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); public delegate bool EP(IntPtr h,IntPtr l);
[DllImport("user32.dll")] public static extern bool EnumWindows(EP f,IntPtr l);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
[StructLayout(LayoutKind.Sequential)] public struct R{public int l,t,r,b;}
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out R r);
public static List<string> L=new List<string>();
public static void Run(string pat){ L.Clear(); EnumWindows((h,l)=>{ if(!IsWindowVisible(h))return true; var s=new StringBuilder(256); GetWindowText(h,s,256); var t=s.ToString(); if(t.Length>0 && t.Contains(pat)){ uint p; GetWindowThreadProcessId(h,out p); R r; GetWindowRect(h,out r); L.Add(p+"|"+h+"|"+t+"|"+r.l+","+r.t+","+r.r+","+r.b);} return true;},IntPtr.Zero);} }
'@

[WL]::SetProcessDPIAware()|Out-Null; [WL]::Run($pat); [WL]::L
