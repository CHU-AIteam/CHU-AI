function New-ComoShortcut([string]$Path, [string]$LaunchAction, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        $shortcut.TargetPath = Get-ComoPowerShell
        $shortcut.Arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument $script:ComoLauncherPath) + ' -Action ' + $LaunchAction
        $shortcut.WorkingDirectory = $script:ComoRoot
        $shortcut.Description = $Description
        $shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',21'
        if ($LaunchAction -eq 'Manage') { $shortcut.Hotkey = 'CTRL+ALT+K' }
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
}

function Initialize-ComoSettings([string]$GeminiKey) {
    foreach ($kind in @('docker', 'desktop', 'edge', 'git')) { Get-ComoExecutable $kind | Out-Null }
    if (-not (Test-Path -LiteralPath $script:ComoEnvPath)) {
        Copy-Item -LiteralPath (Join-Path $script:ComoRoot 'src\.env.example') -Destination $script:ComoEnvPath
    }
    $settings = Get-ComoSettings
    if (-not $settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY') -or $settings.CHAT_LOG_ADMIN_API_KEY -match '^(|change-this-admin-key)$') {
        $bytes = New-Object byte[] 32
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($bytes) } finally { $random.Dispose() }
        Set-ComoSetting 'CHAT_LOG_ADMIN_API_KEY' ([BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant())
    }
    if ($GeminiKey) {
        if ($GeminiKey -notmatch '^[A-Za-z0-9_-]{30,256}$') {
            throw (New-ComoError '担当者から受け取ったコードだけを貼り付けてください。' 'gemini_key')
        }
        Set-ComoSetting 'API_KEY' $GeminiKey
    }
    if ((Get-ComoSettings)['COMO_KIOSK_AUTO_LOGIN'] -ne 'true') { Set-ComoSetting 'COMO_KIOSK_AUTO_LOGIN' 'true' }
}

function Install-ComoShortcuts {
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    New-ComoShortcut (Join-Path $desktop 'コモ管理.lnk') 'Manage' 'コモを開く・案内を更新する・今日の利用を終える'
    $guideUri = [Uri]::new((Join-Path (Split-Path $script:ComoLauncherPath) 'quick-guide.html')).AbsoluteUri
    [IO.File]::WriteAllLines((Join-Path $desktop 'コモの使い方.url'), @('[InternetShortcut]', "URL=$guideUri"), $script:ComoUtf8)
}

function Set-ComoAutoStart([bool]$Enable) {
    $taskName = 'Chat-Como AutoStart ' + (Get-ComoId)
    $startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'コモ自動起動.lnk'
    if (-not $Enable) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
        return 'disabled'
    }
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument $script:ComoLauncherPath) + ' -Action AutoStart'
        $action = New-ScheduledTaskAction -Execute (Get-ComoPowerShell) -Argument $arguments -WorkingDirectory $script:ComoRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Windowsのログイン後にコモを開く' -Force | Out-Null
        if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
        return 'task-scheduler'
    } catch {
        New-ComoShortcut $startupPath 'AutoStart' 'Windowsのログイン後にコモを開く'
        return 'startup-folder'
    }
}

function Write-ComoSetupState([string]$Method, [bool]$Verified) {
    Write-ComoJson $script:ComoSetupPath @{
        auto_start = $Method
        verified = $Verified
        updated_at = [DateTimeOffset]::Now.ToString('o')
    }
}
