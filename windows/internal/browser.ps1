function Initialize-ComoWindowAccess {
    if ('ComoWindowAccess' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public static class ComoWindowAccess
{
    private delegate bool EnumProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder buffer, int size);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] private static extern bool ShowWindowAsync(IntPtr window, int show);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(uint processId);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    public static IntPtr[] Find(string title, string processName)
    {
        var matches = new List<IntPtr>();
        EnumWindows(delegate(IntPtr window, IntPtr data)
        {
            if (!IsWindowVisible(window)) return true;
            var text = new StringBuilder(256);
            GetWindowText(window, text, text.Capacity);
            string caption = text.ToString();
            if (caption != title && !caption.StartsWith(title + " - ", StringComparison.Ordinal)) return true;
            uint pid;
            GetWindowThreadProcessId(window, out pid);
            try
            {
                using (var process = Process.GetProcessById((int)pid))
                {
                    if (String.Equals(process.ProcessName, processName, StringComparison.OrdinalIgnoreCase))
                        matches.Add(window);
                }
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }
            return true;
        }, IntPtr.Zero);
        return matches.ToArray();
    }

    public static bool Activate(IntPtr window)
    {
        if (IsIconic(window)) ShowWindowAsync(window, 9);
        return SetForegroundWindow(window);
    }

    public static void RequestClose(IntPtr window)
    {
        PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero);
    }
}
'@
}

function Get-ComoWindowTitle { return ('Chat-Como [' + (Get-ComoId).ToLowerInvariant() + ']') }

function Close-ComoBrowser {
    Initialize-ComoWindowAccess
    $title = Get-ComoWindowTitle
    foreach ($item in [ComoWindowAccess]::Find($title, 'msedge')) {
        [ComoWindowAccess]::RequestClose($item)
    }
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        if ([ComoWindowAccess]::Find($title, 'msedge').Length -eq 0) { return }
        Start-Sleep -Milliseconds 200
    }
    throw (New-ComoError 'コモの画面を閉じられませんでした。コモの画面を閉じてから「今日の利用を終える」を押してください。' 'browser')
}

function Open-ComoBrowser([switch]$Knowledge, [switch]$Limited, [switch]$Refresh, [switch]$Legacy) {
    $edge = Get-ComoExecutable 'edge'
    if ($Knowledge) {
        $settings = Get-ComoSettings
        $key = [string]$settings['CHAT_LOG_ADMIN_API_KEY']
        if (-not $key) { throw (New-ComoError '初回設定で管理用の接続を準備してください。' 'settings') }
        $url = $script:ComoBaseUrl + '/manage/#key=' + [Uri]::EscapeDataString($key)
        Start-Process -FilePath $edge -ArgumentList (ConvertTo-ComoArgument ('--app=' + $url)) | Out-Null
        return $true
    }

    Initialize-ComoWindowAccess
    if ($Refresh) { Close-ComoBrowser }
    $title = Get-ComoWindowTitle
    $existing = [ComoWindowAccess]::Find($title, 'msedge')
    if ($existing.Length -gt 0) { return [ComoWindowAccess]::Activate($existing[0]) }

    $url = $script:ComoBaseUrl + '/?kiosk=1'
    if ($Legacy) {
        $arguments = @('--new-window', '--start-fullscreen', $url)
    } else {
        $url += '&como_window=' + (Get-ComoId).ToLowerInvariant()
        if ($Limited) { $url += '&chat_paused=1' }
        # Keep the Edge profile and local origin so the existing tally is preserved.
        $arguments = @('--start-fullscreen', ('--app=' + $url))
    }
    $joined = (($arguments | ForEach-Object { ConvertTo-ComoArgument $_ }) -join ' ')
    Start-Process -FilePath $edge -ArgumentList $joined | Out-Null
    if ($Legacy) { return $true }
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        $opened = [ComoWindowAccess]::Find($title, 'msedge')
        if ($opened.Length -gt 0) { return [ComoWindowAccess]::Activate($opened[0]) }
        Start-Sleep -Milliseconds 250
    }
    throw (New-ComoError 'Edgeでコモの画面が開いたことを確認できません。Edgeの初回案内やタスクバーを確認してから再試行してください。' 'browser')
}
