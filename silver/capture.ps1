# Screen capture for the eye (silver/eye.py), run by Windows PowerShell from WSL.
# Each line read on stdin asks for one capture of the primary screen; the reply is the PNG's path.
# It only copies pixels already on screen, like a screenshot: nothing touches the game.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
# DPI-aware, so a scaled display (125%, 150%) is captured at its real resolution
Add-Type 'using System.Runtime.InteropServices; public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[void][Dpi]::SetProcessDPIAware()
$path = Join-Path $env:TEMP 'silver-eye.png'
while ($null -ne [Console]::In.ReadLine()) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    [Console]::Out.WriteLine($path)
    [Console]::Out.Flush()
}
