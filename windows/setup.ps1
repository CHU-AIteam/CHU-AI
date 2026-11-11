#requires -Version 5.1
[CmdletBinding()]
param([switch]$DisableAutoStart)

. (Join-Path $PSScriptRoot 'common.ps1')
if ($env:OS -ne 'Windows_NT') { throw '初回設定は本番のWindows PCで実行してください。' }
Add-Type -AssemblyName PresentationFramework
$taskName = 'Chat-Como AutoStart ' + (Get-ComoId)
$startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'コモ自動起動.lnk'

function New-ComoShortcut([string]$Path, [string]$LaunchAction, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        $shortcut.TargetPath = Get-ComoPowerShell
        $shortcut.Arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument (Join-Path $PSScriptRoot 'launcher.ps1')) + ' -Action ' + $LaunchAction
        $shortcut.WorkingDirectory = $script:ComoRoot
        $shortcut.Description = $Description
        $shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',21'
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
}

try {
    if ($DisableAutoStart) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -ne $task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
        [Windows.MessageBox]::Show('コモの自動起動を解除しました。デスクトップの「コモ管理」は引き続き使えます。', 'コモの初回設定') | Out-Null
        exit 0
    }
    foreach ($kind in @('docker', 'desktop', 'edge', 'git')) { Get-ComoExecutable $kind | Out-Null }
    if (-not (Test-Path -LiteralPath $script:ComoEnvPath)) {
        Copy-Item -LiteralPath (Join-Path $script:ComoRoot 'src\.env.example') -Destination $script:ComoEnvPath
    }
    $settings = Get-ComoSettings
    if (-not $settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY') -or $settings.CHAT_LOG_ADMIN_API_KEY -match '^(|change-this-admin-key)$') {
        $bytes = New-Object byte[] 32
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($bytes) } finally { $random.Dispose() }
        $secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        Set-ComoSetting 'CHAT_LOG_ADMIN_API_KEY' $secret
        $secret = $null
    }
    Set-ComoSetting 'COMO_KIOSK_AUTO_LOGIN' 'true'
    $settings = Get-ComoSettings
    if (-not $settings.ContainsKey('API_KEY') -or $settings.API_KEY -match '^(|your-gemini-api-key)$') {
        [Windows.MessageBox]::Show('初回だけ接続設定が必要です。これから開く設定ファイルの API_KEY に、担当者から受け取ったGemini APIキーを設定して保存してください。設定ファイルは他の人に送らないでください。', 'コモの初回設定') | Out-Null
        Start-Process notepad.exe -ArgumentList (ConvertTo-ComoArgument $script:ComoEnvPath) -Wait
        Assert-ComoSettings
    }

    $desktopPath = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'コモ管理.lnk'
    New-ComoShortcut $desktopPath 'Manage' 'コモの起動・終了、USBからの知識更新'
    $guideShortcut = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'コモの使い方.url'
    $guidePath = Join-Path $PSScriptRoot 'quick-guide.html'
    $guideUri = (New-Object Uri($guidePath)).AbsoluteUri
    [IO.File]::WriteAllText($guideShortcut, "[InternetShortcut]`r`nURL=$guideUri`r`n", $script:ComoUtf8)

    $autoStartMethod = 'task-scheduler'
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument (Join-Path $PSScriptRoot 'launcher.ps1')) + ' -Action AutoStart'
        $taskAction = New-ScheduledTaskAction -Execute (Get-ComoPowerShell) -Argument $arguments -WorkingDirectory $script:ComoRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $taskSettings -Description 'Windowsログイン後にコモを起動してEdgeで全画面表示します。' -Force | Out-Null
        if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
    } catch {
        # Some school PCs deny registering tasks for standard users. Startup is the
        # same per-user logon behavior and does not require an administrator password.
        New-ComoShortcut $startupPath 'AutoStart' 'Windowsログイン後にコモを起動'
        $autoStartMethod = 'startup-folder'
        Write-ComoLog 'タスクの登録が許可されないため、スタートアップフォルダーへ自動起動を登録しました。'
    }
    Write-ComoJson (Join-Path $script:ComoStateDir 'setup.json') @{ repository = $script:ComoRoot; task_name = $taskName; autostart = $autoStartMethod; configured_at = [DateTimeOffset]::Now.ToString('o') }
    Write-ComoLog 'Windows初回設定が完了しました。'
    [Windows.MessageBox]::Show('設定が完了しました。次回のWindowsログインからコモが自動で開きます。今すぐ使う場合は、デスクトップの「コモ管理」から「コモを開く」を押してください。初回は知識の準備に数分かかります。', 'コモの初回設定') | Out-Null
    $arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument (Join-Path $PSScriptRoot 'launcher.ps1')) + ' -Action Manage'
    Start-Process -FilePath (Get-ComoPowerShell) -ArgumentList $arguments -WindowStyle Hidden | Out-Null
} catch {
    $message = Protect-ComoText $_.Exception.Message
    Write-ComoLog ('初回設定が完了しませんでした: ' + $message)
    [Windows.MessageBox]::Show(('初回設定が完了しませんでした。' + [Environment]::NewLine + $message), 'コモの初回設定', 'OK', 'Error') | Out-Null
    exit 1
}
