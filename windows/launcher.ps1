#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Manage', 'AutoStart', 'Setup', 'Open', 'Limited', 'Restart', 'Stop', 'Knowledge', 'CheckUpdate', 'Update', 'Rollback', 'Status', 'DisableAutoStart')]
    [string]$Action = 'Manage',
    [switch]$Worker,
    [switch]$NavigateOnly,
    [switch]$NoAutoStart
)

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'internal\installation.ps1')
. (Join-Path $PSScriptRoot 'internal\release.ps1')
. (Join-Path $PSScriptRoot 'internal\browser.ps1')
$script:ComoLauncherPath = Join-Path $PSScriptRoot 'launcher.ps1'
$script:ComoFailurePath = Join-Path $script:ComoStateDir 'last_failure.json'
$script:ComoStartupActions = @('Setup', 'Open', 'Restart', 'Update', 'Rollback')
if ($env:OS -ne 'Windows_NT') { throw 'コモ管理はWindows専用です。' }

function Show-ComoVisitor([string]$Operation, $Health, [switch]$Limited, [switch]$Refresh) {
    Write-ComoStep $Operation '画面を開く' '来場者が使うコモの画面を開いています。'
    $legacy = -not (Get-ComoValue $Health 'windows_revision' '')
    $foreground = Open-ComoBrowser -Limited:$Limited -Refresh:$Refresh -Legacy:$legacy
    if (-not $foreground) { return '画面の下に並んでいるアイコンから、コモの画面を選んでください。' }
    if ($Limited) { return 'コモとおみくじを開きました。いまは質問への回答をお休みしています。' }
    return 'コモを使えます。'
}

if ($Worker) {
    $key = $env:COMO_INSTALL_KEY
    Remove-Item Env:COMO_INSTALL_KEY -ErrorAction SilentlyContinue
    if ($Action -eq 'Status') { Update-ComoRuntime; exit 0 }
    if ($NavigateOnly -and $Action -eq 'Knowledge') {
        try {
            Wait-ComoHealth | Out-Null
            Open-ComoBrowser -Knowledge | Out-Null
            exit 0
        } catch {
            Write-ComoLog ('知識画面を開けませんでした: ' + $_.Exception.Message)
            exit 1
        }
    }
    $mutex = New-Object Threading.Mutex($false, ('Local\ComoOperation-' + (Get-ComoId)))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { exit 2 }
        Write-ComoLog ('操作開始: ' + $Action)
        Write-ComoStep $Action '準備' '準備を始めました。'
        switch ($Action) {
            'Setup' {
                Assert-ComoKnowledgeIdle
                Write-ComoStep $Action 'パソコンの準備' 'このパソコンでコモを使うための準備を確かめています。'
                Initialize-ComoSettings $key
                $key = $null
                Install-ComoShortcuts
                $health = Start-ComoApplication $Action -CheckChat -ApplyCheckout
                $opened = Show-ComoVisitor $Action $health -Refresh
                $method = Set-ComoAutoStart (-not $NoAutoStart)
                Write-ComoSetupState $method $true
                $message = '試しの質問に答えられました。' + $opened
            }
            'DisableAutoStart' {
                $method = Set-ComoAutoStart $false
                $setup = Read-ComoJson $script:ComoSetupPath
                Write-ComoSetupState $method ([bool](Get-ComoValue $setup 'verified' $false))
                $message = 'パソコンにログインしたとき、自動でコモを開く設定を止めました。'
            }
            'Stop' {
                Assert-ComoKnowledgeIdle
                Write-ComoStep $Action 'コモを閉じる' '今日の利用を終えています。少しお待ちください。'
                Close-ComoBrowser
                Invoke-ComoCompose @('stop') | Out-Null
                $message = '今日の利用を終えました。パソコンの電源を切って大丈夫です。'
            }
            'Restart' {
                $health = Start-ComoApplication $Action -Restart
                $message = Show-ComoVisitor $Action $health -Refresh
            }
            'Knowledge' {
                Start-ComoApplication $Action -HealthOnly | Out-Null
                Open-ComoBrowser -Knowledge | Out-Null
                $message = '案内を更新する画面を開きました。'
            }
            'Limited' {
                $health = Get-ComoHealth
                if ($null -eq $health -or -not (Get-ComoValue $health 'windows_revision' '')) {
                    throw (New-ComoError 'いまはコモの画面を開けません。「コモを開く」を押してから、画面の案内に沿って進んでください。' 'local_connection')
                }
                Set-ComoKioskPaused $true
                $message = Show-ComoVisitor $Action $health -Limited
            }
            'CheckUpdate' {
                Write-ComoStep $Action '新しい更新を探す' '新しいコモが届いているか調べています。'
                $message = Check-ComoUpdate
            }
            'Update' {
                Assert-ComoKnowledgeIdle
                Assert-ComoSettings
                Write-ComoStep $Action '新しいコモを受け取る' '更新に必要なものを受け取っています。'
                Update-ComoCode
                $health = Start-ComoApplication $Action -ApplyCheckout -CheckChat
                $opened = Show-ComoVisitor $Action $health -Refresh
                $message = 'コモ本体を更新しました。' + $opened
            }
            'Rollback' {
                $health = Start-ComoApplication $Action -Rollback
                $opened = Show-ComoVisitor $Action $health -Refresh
                $message = 'ひとつ前のコモに戻しました。' + $opened
            }
            default {
                $health = Start-ComoApplication $Action
                $message = Show-ComoVisitor $Action $health
            }
        }
        $lastIssue = Read-ComoJson $script:ComoFailurePath
        $failedAction = Get-ComoValue $lastIssue 'operation' ''
        if (($Action -eq $failedAction -or ($Action -eq 'Setup') -or
            ($Action -in @('Open', 'Restart') -and $failedAction -in @('Open', 'Restart')))) {
            Write-ComoJson $script:ComoFailurePath @{}
        }
        Write-ComoOperation 'succeeded' $message $Action
        try { Update-ComoRuntime } catch { }
        Write-ComoLog ('操作完了: ' + $Action)
        exit 0
    } catch {
        $message = Protect-ComoText $_.Exception.Message
        $code = [string]$_.Exception.Data['como_code']
        if (-not $code) { $code = 'windows' }
        Write-ComoLog ('操作失敗: ' + $Action + ' [' + $code + '] ' + $message)
        Write-ComoOperation 'failed' $message $Action $code
        if ($Action -in $script:ComoStartupActions) {
            Write-ComoJson $script:ComoFailurePath (Read-ComoJson $script:ComoOperationPath)
        }
        try { Update-ComoRuntime } catch { }
        exit 1
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$guiMutex = New-Object Threading.Mutex($false, ('Local\ComoManager-' + (Get-ComoId)))
$setupRequest = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::AutoReset, ('Local\ComoShowSetup-' + (Get-ComoId)))
$guiLocked = $false
try { $guiLocked = $guiMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $guiLocked = $true }
if (-not $guiLocked) {
    if ($Action -eq 'Setup') { [void]$setupRequest.Set() }
    Initialize-ComoWindowAccess
    foreach ($item in [ComoWindowAccess]::Find('コモ管理', 'powershell')) {
        [void][ComoWindowAccess]::Activate($item)
    }
    $guiMutex.Dispose()
    $setupRequest.Dispose()
    exit 0
}

[xml]$xaml = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'internal\manager.xaml'), $script:ComoUtf8)
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$controls = @{}
foreach ($attribute in $xaml.SelectNodes("//@*[local-name()='Name']")) {
    $controls[$attribute.Value] = $window.FindName($attribute.Value)
}
$script:ComoActiveWorker = $null
$script:ComoStatusWorker = $null
$script:ComoKnowledgeWorker = $null
$script:ComoRetryAction = ''
$script:ComoRetryCode = ''
$script:ComoLastStatusAt = [DateTime]::MinValue
$script:ComoScreenMessage = ''
$script:ComoCloseWhenReady = ($Action -eq 'AutoStart')
$script:ComoPendingAction = if ($Action -eq 'AutoStart') { 'Open' } elseif ($Action -in @('Setup', 'Manage')) { '' } else { $Action }
$controls.AutoStartCheck.IsChecked = (Get-ComoValue (Read-ComoJson $script:ComoSetupPath) 'auto_start' '') -ne 'disabled'

function New-ComoWorker([string]$WorkerAction, [switch]$NavigationOnly, [string]$GeminiKey = '', [switch]$DisableAutoStart) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Get-ComoPowerShell
    $info.Arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument $script:ComoLauncherPath) + ' -Action ' + $WorkerAction + ' -Worker'
    if ($NavigationOnly) { $info.Arguments += ' -NavigateOnly' }
    if ($DisableAutoStart) { $info.Arguments += ' -NoAutoStart' }
    $info.WorkingDirectory = $script:ComoRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    if ($GeminiKey) { $info.EnvironmentVariables['COMO_INSTALL_KEY'] = $GeminiKey }
    $process = [Diagnostics.Process]::Start($info)
    try {
        Initialize-ComoWindowAccess
        [void][ComoWindowAccess]::AllowSetForegroundWindow([uint32]$process.Id)
    } catch { }
    return $process
}

function Show-ComoKeyEntry {
    $controls.ChangeKeyButton.Visibility = 'Collapsed'
    $controls.KeyEntryPanel.Visibility = 'Visible'
    [void]$controls.GeminiKey.Focus()
}

function Show-ComoDailyView {
    $controls.SetupPanel.Visibility = 'Collapsed'
    $controls.GeminiKey.Clear()
    $controls.ScreenHeading.Text = 'コモ管理'
    $controls.DailyPanel.Visibility = 'Visible'
}

function Test-ComoSetupKey {
    $settings = Get-ComoSettings
    return ($settings.ContainsKey('API_KEY') -and $settings.API_KEY -notmatch '^(|your-gemini-api-key)$')
}

function Show-ComoSetup([switch]$ChangeKey) {
    $hasKey = Test-ComoSetupKey
    $script:ComoScreenMessage = ''
    $controls.ScreenHeading.Text = 'コモの初回設定'
    $controls.DailyPanel.Visibility = 'Collapsed'
    $controls.Maintenance.IsExpanded = $false
    $controls.KeyHint.Text = if ($hasKey) {
        'このパソコンに、コモをつなぐためのコードが入っています。'
    } else { 'コモを使うために、初めてのときだけコードを入れます。' }
    $controls.SetupPanel.Visibility = 'Visible'
    $controls.ChangeKeyButton.Visibility = if ($hasKey -and -not $ChangeKey) { 'Visible' } else { 'Collapsed' }
    $controls.KeyEntryPanel.Visibility = 'Collapsed'
    if (-not $hasKey -or $ChangeKey) { Show-ComoKeyEntry }
    $controls.SetupPanel.BringIntoView()
}

function Invoke-ComoGuiAction([string]$SelectedAction, [string]$GeminiKey = '', [switch]$DisableAutoStart) {
    if ($null -ne $script:ComoActiveWorker) { return }
    $script:ComoScreenMessage = ''
    try {
        $script:ComoActiveWorker = New-ComoWorker $SelectedAction -GeminiKey $GeminiKey -DisableAutoStart:$DisableAutoStart
        $controls.StatusText.Text = '操作を開始しています。'
        $controls.BusyBar.Visibility = 'Visible'
    } catch {
        $script:ComoScreenMessage = '操作を開始できませんでした。コモ管理を開き直してください。'
    }
}

function Show-ComoDiagnostic {
    $picker = New-Object Microsoft.Win32.SaveFileDialog
    $picker.Title = '担当者に渡す記録を保存'
    $picker.FileName = 'コモの記録-' + (Get-Date -Format 'yyyyMMdd-HHmm') + '.txt'
    $picker.Filter = 'テキスト ファイル (*.txt)|*.txt'
    if ($picker.ShowDialog($window) -ne $true) { return }
    try {
        $runtime = Read-ComoJson $script:ComoRuntimePath
        $operation = Read-ComoJson $script:ComoOperationPath
        $failure = Read-ComoJson $script:ComoFailurePath
        $report = @(
            'Chat-Como 設置PCの診断記録',
            ('作成: ' + [DateTimeOffset]::Now.ToString('o')),
            ('取得した版: ' + (Get-ComoValue $runtime 'installed_revision')),
            ('実行中の版: ' + (Get-ComoValue $runtime 'running_revision')),
            ('状態: ' + (Get-ComoValue $runtime 'message'))
        )
        foreach ($entry in @($operation, $failure)) {
            if (-not (Get-ComoValue $entry 'updated_at')) { continue }
            $report += @(
                '',
                ('日時: ' + (Get-ComoValue $entry 'updated_at')),
                ('操作: ' + (Get-ComoValue $entry 'operation') + ' / ' + (Get-ComoValue $entry 'step')),
                ('結果: ' + (Get-ComoValue $entry 'phase') + ' / ' + (Get-ComoValue $entry 'code')),
                (Get-ComoValue $entry 'message')
            )
        }
        [IO.File]::WriteAllText($picker.FileName, (Protect-ComoText ($report -join [Environment]::NewLine)), [Text.UTF8Encoding]::new($true))
        $script:ComoScreenMessage = '記録を保存しました。保存したファイルを担当者に渡してください。'
    } catch {
        $script:ComoScreenMessage = '記録を保存できません。書き込める場所を選び直してください。'
    }
}

function Get-ComoRunningSnapshot($Operation) {
    if ((Get-ComoValue $Operation 'phase') -ne 'running') { return $false }
    try {
        $process = Get-Process -Id ([int](Get-ComoValue $Operation 'process_id')) -ErrorAction Stop
        $started = [DateTimeOffset]::Parse((Get-ComoValue $Operation 'started_at'))
        return ($process.ProcessName -eq 'powershell' -and
            [Math]::Abs(($process.StartTime - $started.LocalDateTime).TotalSeconds) -lt 60)
    } catch { return $false }
}

$controls.OpenButton.Add_Click({ Invoke-ComoGuiAction 'Open' })
$controls.StopButton.Add_Click({ Invoke-ComoGuiAction 'Stop' })
$controls.LimitedButton.Add_Click({ Invoke-ComoGuiAction 'Limited' })
$controls.RestartButton.Add_Click({ Invoke-ComoGuiAction 'Restart' })
$controls.CheckUpdateButton.Add_Click({ Invoke-ComoGuiAction 'CheckUpdate' })
$controls.SetupDetailsButton.Add_Click({ Show-ComoSetup })
$controls.HideSetupButton.Add_Click({ $script:ComoScreenMessage = ''; Show-ComoDailyView })
$controls.ChangeKeyButton.Add_Click({ Show-ComoKeyEntry })
$controls.DiagnosticButton.Add_Click({ Show-ComoDiagnostic })
$controls.RecoveryDiagnosticButton.Add_Click({ Show-ComoDiagnostic })
$controls.GuideButton.Add_Click({ Start-Process (Join-Path $PSScriptRoot 'quick-guide.html') })
$controls.SetupButton.Add_Click({
    $key = $controls.GeminiKey.Password.Trim()
    if ($controls.KeyEntryPanel.Visibility -eq 'Visible' -and -not $key) {
        $script:ComoScreenMessage = '担当者から受け取ったコードを入力欄に貼り付けてください。'
        [void]$controls.GeminiKey.Focus()
        return
    }
    $controls.GeminiKey.Clear()
    $controls.SetupPanel.Visibility = 'Collapsed'
    Invoke-ComoGuiAction 'Setup' -GeminiKey $key -DisableAutoStart:($controls.AutoStartCheck.IsChecked -ne $true)
})
$controls.RetryButton.Add_Click({
    if ($script:ComoRetryCode -eq 'gemini_key') { Show-ComoSetup -ChangeKey }
    elseif ($script:ComoRetryCode -eq 'settings') { Show-ComoSetup }
    elseif ($script:ComoRetryAction -eq 'Setup') {
        if (Test-ComoSetupKey) {
            Invoke-ComoGuiAction 'Setup' -DisableAutoStart:($controls.AutoStartCheck.IsChecked -ne $true)
        } else { Show-ComoSetup }
    }
    elseif ($script:ComoRetryAction) { Invoke-ComoGuiAction $script:ComoRetryAction }
})
$controls.KnowledgeButton.Add_Click({
    $script:ComoCloseWhenReady = $false
    if ($null -ne $script:ComoActiveWorker -or (Get-ComoRunningSnapshot (Read-ComoJson $script:ComoOperationPath))) {
        if ($null -eq $script:ComoKnowledgeWorker) {
            try { $script:ComoKnowledgeWorker = New-ComoWorker 'Knowledge' -NavigationOnly }
            catch { $script:ComoScreenMessage = '案内を更新する画面を開けませんでした。少し待って「案内を更新する」を押してください。' }
        }
    } else { Invoke-ComoGuiAction 'Knowledge' }
})
$controls.UpdateButton.Add_Click({
    $answer = [Windows.MessageBox]::Show($window, 'コモの画面を開き直します。来場者が使っていないことを確かめてから進んでください。', 'コモ本体を更新する', 'OKCancel', 'Information')
    if ($answer -eq 'OK') { Invoke-ComoGuiAction 'Update' }
})
$controls.RollbackButton.Add_Click({
    $answer = [Windows.MessageBox]::Show($window, 'ひとつ前のコモで開き直します。案内とおみくじの回数は残ります。', 'ひとつ前のコモに戻す', 'OKCancel', 'Information')
    if ($answer -eq 'OK') { Invoke-ComoGuiAction 'Rollback' }
})

$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(400)
$timer.Add_Tick({
    if ($script:ComoPendingAction) {
        $selected = $script:ComoPendingAction
        $script:ComoPendingAction = ''
        Invoke-ComoGuiAction $selected
    }
    foreach ($slot in @('ComoActiveWorker', 'ComoStatusWorker', 'ComoKnowledgeWorker')) {
        $process = Get-Variable -Name $slot -Scope Script -ValueOnly
        if ($null -eq $process -or -not $process.HasExited) { continue }
        $exit = $process.ExitCode
        $workerId = $process.Id
        $process.Dispose()
        Set-Variable -Name $slot -Scope Script -Value $null
        if ($slot -eq 'ComoKnowledgeWorker' -and $exit -ne 0) {
            $script:ComoScreenMessage = '案内を更新する画面を開けませんでした。少し待って「案内を更新する」を押してください。'
        }
        if ($slot -eq 'ComoActiveWorker') {
            $script:ComoLastStatusAt = [DateTime]::MinValue
            if ($controls.SetupPanel.Visibility -ne 'Visible') { Show-ComoDailyView }
            if ($exit -eq 2) { $script:ComoScreenMessage = '別の操作が進行中です。少しお待ちください。' }
            elseif ($exit -ne 0) {
                $finished = Read-ComoJson $script:ComoOperationPath
                if ((Get-ComoValue $finished 'process_id') -ne $workerId -or (Get-ComoValue $finished 'phase') -ne 'failed') {
                    $script:ComoScreenMessage = '結果を確かめられませんでした。「設定・困ったとき」から担当者に渡す記録を保存できます。'
                }
            }
            if ($script:ComoCloseWhenReady -and $exit -eq 0) { $window.Close(); return }
            $script:ComoCloseWhenReady = $false
        }
    }

    $operation = Read-ComoJson $script:ComoOperationPath
    $phase = Get-ComoValue $operation 'phase' ''
    $running = ($null -ne $script:ComoActiveWorker) -or (Get-ComoRunningSnapshot $operation)
    if (-not $running -and $setupRequest.WaitOne(0)) { Show-ComoSetup }
    $interrupted = $phase -eq 'running' -and -not $running
    $failure = Read-ComoJson $script:ComoFailurePath
    if ($phase -eq 'failed' -or $interrupted) { $failure = $operation }
    $issueText = Get-ComoFailureNotice $failure
    $controls.RuntimeText.Text = 'コモを使えるか確認しています。'
    $runtime = Read-ComoJson $script:ComoRuntimePath
    if ($null -ne $runtime) { $controls.RuntimeText.Text = [string](Get-ComoValue $runtime 'message' '') }

    $controls.StatusText.Text = if ($script:ComoScreenMessage) { $script:ComoScreenMessage }
        elseif ($interrupted) { '前回の操作が途中で止まりました。もう一度試せます。' }
        elseif ($running -or $phase -eq 'succeeded') { [string](Get-ComoValue $operation 'message' '操作を開始しています。') }
        else { '' }
    $controls.StepText.Text = ''
    if ($running) {
        $controls.StepText.Text = [string](Get-ComoValue $operation 'step' '準備')
        try {
            $started = [DateTimeOffset]::Parse((Get-ComoValue $operation 'started_at'))
            $elapsed = [Math]::Max(0, [int]([DateTimeOffset]::Now - $started).TotalSeconds)
            $controls.StepText.Text += ' ／ 経過 ' + [Math]::Floor($elapsed / 60) + '分' + ($elapsed % 60) + '秒'
        } catch { }
    }
    $controls.BusyBar.Visibility = if ($running) { 'Visible' } else { 'Collapsed' }
    $progress = [int](Get-ComoValue $operation 'progress' -1)
    $controls.BusyBar.IsIndeterminate = $progress -lt 0
    if ($progress -ge 0) { $controls.BusyBar.Value = [Math]::Min(100, $progress) }

    $controls.UpdatedText.Text = ''
    try {
        $timestamp = [DateTimeOffset]::Parse((Get-ComoValue $runtime 'last_updated')).ToLocalTime()
        $controls.UpdatedText.Text = '案内を最後に変えた日 ' + $timestamp.ToString('yyyy/MM/dd HH:mm')
    } catch { }
    $installed = [string](Get-ComoValue $runtime 'installed_revision' '')
    $active = [string](Get-ComoValue $runtime 'running_revision' '')
    $previous = [string](Get-ComoValue $runtime 'previous_revision' '')
    $auto = [string](Get-ComoValue $runtime 'auto_start' '')
    $autoText = if ($auto -in @('task-scheduler', 'startup-folder')) { '登録済み' } else { '未登録' }
    $controls.VersionText.Text = '取得した版 ' + $(if ($installed) { $installed } else { '—' }) +
        ' ／ 実行中 ' + $(if ($active) { $active } else { '—' }) + ' ／ 自動起動 ' + $autoText
    if ($previous) { $controls.VersionText.Text += ' ／ 戻せる版 ' + $previous }
    if (Get-ComoValue $runtime 'failed_revision' '') { $controls.VersionText.Text += ' ／ 最後に失敗した版 ' + (Get-ComoValue $runtime 'failed_revision') }

    $controls.RecoveryPanel.Visibility = if (-not $running -and $controls.SetupPanel.Visibility -ne 'Visible' -and ($issueText -or $interrupted)) { 'Visible' } else { 'Collapsed' }
    $controls.FailureText.Text = $issueText
    if ($interrupted) { $controls.FailureText.Text = '前回の操作の続きを試してください。' }
    $script:ComoRetryAction = [string](Get-ComoValue $failure 'operation' '')
    $script:ComoRetryCode = [string](Get-ComoValue $failure 'code' '')
    $controls.RetryButton.Content = if ($script:ComoRetryCode -eq 'gemini_key') { 'つなぐためのコードを見直す' }
        elseif ($script:ComoRetryCode -eq 'settings') { '最初の設定を見直す' }
        else { 'もう一度試す' }
    $controls.RetryButton.IsEnabled = $script:ComoRetryAction -in @('Setup', 'Open', 'Restart', 'Stop', 'Knowledge', 'CheckUpdate', 'Update', 'Rollback', 'Limited')
    $controls.LimitedButton.Visibility = if ((Get-ComoValue $runtime 'chat_paused' $false) -and $active -and $active -ne '旧版') { 'Visible' } else { 'Collapsed' }
    foreach ($name in @('OpenButton', 'StopButton', 'RestartButton', 'CheckUpdateButton', 'UpdateButton', 'SetupButton', 'SetupDetailsButton')) {
        $controls[$name].IsEnabled = -not $running
    }
    $controls.KnowledgeButton.IsEnabled = ($null -eq $script:ComoKnowledgeWorker) -and (-not $running -or (Get-ComoValue $operation 'operation') -ne 'Stop')
    $controls.RollbackButton.IsEnabled = -not $running -and [bool]$previous
    if ($null -eq $script:ComoStatusWorker -and -not $running -and ([DateTime]::Now - $script:ComoLastStatusAt).TotalSeconds -ge 10) {
        $script:ComoLastStatusAt = [DateTime]::Now
        try { $script:ComoStatusWorker = New-ComoWorker 'Status' } catch { }
    }
})

try {
    if ($Action -eq 'Setup') { Show-ComoSetup }
    $timer.Start()
    [void]$window.ShowDialog()
} finally {
    $timer.Stop()
    foreach ($process in @($script:ComoActiveWorker, $script:ComoStatusWorker, $script:ComoKnowledgeWorker)) {
        if ($null -ne $process) { $process.Dispose() }
    }
    if ($guiLocked) { $guiMutex.ReleaseMutex() }
    $guiMutex.Dispose()
    $setupRequest.Dispose()
}
