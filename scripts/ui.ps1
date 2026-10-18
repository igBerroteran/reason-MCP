# UI helpers for driving Reason's custom-drawn window: focus, click, type, screenshot.
# Usage: . .\ui.ps1 ; Focus-Reason ; Click-At 111 141 ; Send-Text "Kong" ; Save-Shot "path.png"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class RU {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int c);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
 [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
 public delegate bool EnumProc(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
 public struct RECT { public int L,T,R,B; }
 // Largest visible titled top-level window of the process (MainWindowHandle can be a tooltip).
 public static IntPtr MainOf(uint pid) {
   IntPtr best = IntPtr.Zero; long area = -1;
   EnumWindows((h, l) => {
     uint p; GetWindowThreadProcessId(h, out p);
     if (p == pid && IsWindowVisible(h) && GetWindowTextLength(h) > 0) {
       RECT r; GetWindowRect(h, out r); long a = (long)(r.R - r.L) * (r.B - r.T);
       if (IsIconic(h)) a = 1;
       if (a > area) { area = a; best = h; }
     }
     return true; }, IntPtr.Zero);
   return best;
 } }
"@
[RU]::SetProcessDPIAware() | Out-Null

function Get-ReasonWindow { [RU]::MainOf([uint32](Get-Process Reason | Select-Object -First 1).Id) }

function Focus-Reason {
  $h = Get-ReasonWindow
  [RU]::keybd_event(0x12,0,0,[UIntPtr]::Zero); [RU]::keybd_event(0x12,0,2,[UIntPtr]::Zero)
  if ([RU]::IsIconic($h)) { [RU]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 500 }
  [RU]::ShowWindow($h, 3) | Out-Null
  [RU]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 400
  if ([RU]::GetForegroundWindow() -ne $h) { throw "Reason is not in the foreground" }
  $r = New-Object RU+RECT; [RU]::GetWindowRect($h, [ref]$r) | Out-Null
  if ($r.L -lt -100 -or $r.T -lt -100) { throw "Reason window is not visible ($($r.L),$($r.T))" }
}

# Coordinates are relative to the screenshot (window image), converted to screen coordinates.
function To-Screen($x, $y) { $r = New-Object RU+RECT; [RU]::GetWindowRect((Get-ReasonWindow), [ref]$r) | Out-Null; @(($x + $r.L), ($y + $r.T)) }

function Click-At($x, $y, [switch]$Double) {
  Focus-Reason
  $s = To-Screen $x $y
  [RU]::SetCursorPos($s[0], $s[1]) | Out-Null; Start-Sleep -Milliseconds 80
  $n = 1; if ($Double) { $n = 2 }
  for ($i = 0; $i -lt $n; $i++) { [RU]::mouse_event(2,0,0,0,[UIntPtr]::Zero); [RU]::mouse_event(4,0,0,0,[UIntPtr]::Zero); Start-Sleep -Milliseconds 60 }
  Start-Sleep -Milliseconds 300
}

function Send-Text($text) { Focus-Reason; [System.Windows.Forms.SendKeys]::SendWait($text); Start-Sleep -Milliseconds 400 }

function Save-Shot($path) {
  $r = New-Object RU+RECT; [RU]::GetWindowRect((Get-ReasonWindow), [ref]$r) | Out-Null
  $bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
  $bmp.Save($path); $g.Dispose(); $bmp.Dispose()
}
