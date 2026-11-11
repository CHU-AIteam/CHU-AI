#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Manage', 'AutoStart', 'Open', 'Restart', 'Stop', 'Knowledge', 'Update', 'Status')]
    [string]$Action = 'Manage',
    [switch]$Worker,
    [switch]$NavigateOnly
)

. (Join-Path $PSScriptRoot 'common.ps1')
$script:ComoLauncherPath = Join-Path $PSScriptRoot 'launcher.ps1'
if ($env:OS -ne 'Windows_NT') { throw 'このランチャーはWindows専用です。' }

if ($Worker) {
    if ($Action -eq 'Status') { Update-ComoRuntime; exit 0 }
    if ($NavigateOnly -and $Action -eq 'Knowledge') {
        try {
            Wait-ComoHealth | Out-Null
            Open-ComoBrowser -Knowledge
            exit 0
        } catch {
            Write-ComoLog ('知識画面を開けませんでした: ' + (Protect-ComoText $_.Exception.Message))
            exit 1
        }
    }
    $mutex = New-Object Threading.Mutex($false, ('Local\ComoOperation-' + (Get-ComoId)))
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { exit 2 }
        Write-ComoLog ('操作開始: ' + $Action)
        Write-ComoOperation 'running' '操作を準備しています。' $Action
        switch ($Action) {
            'Stop' {
                Assert-ComoKnowledgeIdle
                Write-ComoOperation 'running' 'コモを終了しています。保存した知識や記録は残ります。' $Action
                Invoke-ComoCompose @('stop') | Out-Null
                Write-ComoOperation 'succeeded' 'コモを終了しました。WindowsのスタートメニューからPCをシャットダウンできます。' $Action
            }
            'Restart' {
                Assert-ComoKnowledgeIdle
                Write-ComoOperation 'running' 'コモを再起動しています。' $Action
                Wait-ComoDocker
                Invoke-ComoCompose @('stop') | Out-Null
                Start-ComoServices $Action | Out-Null
                Open-ComoBrowser
                Write-ComoOperation 'succeeded' 'コモを再起動しました。' $Action
            }
            'Knowledge' {
                Start-ComoServices $Action -SkipKnowledgePreparation | Out-Null
                Open-ComoBrowser -Knowledge
                Write-ComoOperation 'succeeded' '知識の更新画面を開きました。USB内のファイルを選んでください。' $Action
            }
            'Update' {
                Assert-ComoKnowledgeIdle
                Assert-ComoSettings
                Update-ComoCode
                Start-ComoServices $Action | Out-Null
                Open-ComoBrowser
                Write-ComoOperation 'succeeded' 'アプリの更新が完了しました。' $Action
            }
            default {
                Start-ComoServices $Action | Out-Null
                Open-ComoBrowser
                Write-ComoOperation 'succeeded' 'コモを利用できます。' $Action
            }
        }
        Update-ComoRuntime
        Write-ComoLog ('操作完了: ' + $Action)
        exit 0
    } catch {
        $message = Protect-ComoText $_.Exception.Message
        Write-ComoLog ('操作失敗: ' + $Action + ' ' + $message)
        Write-ComoOperation 'failed' $message $Action
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
$guiLocked = $false
try { $guiLocked = $guiMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $guiLocked = $true }
if (-not $guiLocked) {
    [Windows.MessageBox]::Show('コモ管理はすでに開いています。タスクバーの「コモ管理」を選んでください。', 'コモ管理') | Out-Null
    $guiMutex.Dispose()
    exit 0
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title="コモ管理"
        Width="650" Height="600" MinWidth="580" MinHeight="560" WindowStartupLocation="CenterScreen"
        Background="#F3F6FA" FontFamily="Yu Gothic UI" FontSize="15">
  <Window.Resources>
    <Style TargetType="Button">
      <Setter Property="MinHeight" Value="54"/><Setter Property="Margin" Value="5"/>
      <Setter Property="Padding" Value="12,8"/><Setter Property="FontSize" Value="17"/>
      <Setter Property="FontWeight" Value="SemiBold"/><Setter Property="Background" Value="White"/>
      <Setter Property="BorderBrush" Value="#D4DEEB"/><Setter Property="Foreground" Value="#24436B"/>
    </Style>
  </Window.Resources>
  <Grid Margin="26">
    <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
    <StackPanel Grid.Row="0" Margin="4,0,4,18">
      <TextBlock Text="コモ管理" FontSize="30" FontWeight="Bold" Foreground="#194596"/>
      <TextBlock Text="起動・終了と、USBからの知識更新はこちらから。" Margin="0,5,0,0" Foreground="#57677D"/>
    </StackPanel>
    <Border Grid.Row="1" Padding="18" Margin="4,0,4,14" CornerRadius="12" Background="#E8F0FC">
      <StackPanel>
        <TextBlock x:Name="StatusText" Text="状態を確認しています。" FontSize="20" FontWeight="SemiBold" Foreground="#24436B" TextWrapping="Wrap"/>
        <ProgressBar x:Name="BusyBar" Height="5" IsIndeterminate="True" Margin="0,13,0,0" Visibility="Collapsed"/>
        <TextBlock x:Name="UpdatedText" Text="" Margin="0,9,0,0" FontSize="12" Foreground="#596D88" TextWrapping="Wrap"/>
      </StackPanel>
    </Border>
    <UniformGrid x:Name="ButtonsPanel" Grid.Row="2" Columns="2" Rows="3">
      <Button x:Name="OpenButton" Content="コモを開く" Background="#2459BD" Foreground="White"/>
      <Button x:Name="KnowledgeButton" Content="知識を更新"/>
      <Button x:Name="RestartButton" Content="コモを再起動"/>
      <Button x:Name="StopButton" Content="本日の利用を終了"/>
      <Button x:Name="UpdateButton" Content="アプリを更新"/>
      <Button x:Name="LogsButton" Content="操作記録を開く"/>
    </UniformGrid>
    <StackPanel Grid.Row="3" Margin="4,14,4,0">
      <TextBlock Text="知識更新：USBを挿す →「知識を更新」→ ファイルを選ぶ → 内容を確認して反映" FontSize="13" Foreground="#57677D" TextWrapping="Wrap"/>
      <TextBlock Text="全画面表示を閉じるときは Alt + F4。管理画面は再びデスクトップのアイコンから開けます。" FontSize="12" Foreground="#6C7D94" Margin="0,7,0,0" TextWrapping="Wrap"/>
    </StackPanel>
  </Grid>
</Window>
'@
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$statusText = $window.FindName('StatusText')
$updatedText = $window.FindName('UpdatedText')
$busyBar = $window.FindName('BusyBar')
$buttonNames = @('OpenButton', 'KnowledgeButton', 'RestartButton', 'StopButton', 'UpdateButton')
$script:ComoActiveWorker = $null
$script:ComoStatusWorker = $null
$script:ComoKnowledgeWorker = $null
$script:ComoLastStatusAt = [DateTime]::MinValue
$script:ComoPendingAction = if ($Action -eq 'AutoStart') { 'Open' } elseif ($Action -eq 'Manage') { '' } else { $Action }
$script:ComoFailure = ''
$script:ComoCloseWhenReady = ($Action -eq 'AutoStart')

function New-ComoWorker([string]$WorkerAction, [switch]$NavigationOnly) {
    $arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + (ConvertTo-ComoArgument $script:ComoLauncherPath) + ' -Action ' + $WorkerAction + ' -Worker'
    if ($NavigationOnly) { $arguments += ' -NavigateOnly' }
    return (Start-Process -FilePath (Get-ComoPowerShell) -ArgumentList $arguments -WindowStyle Hidden -PassThru)
}

function Set-ComoButtons([bool]$Enabled) {
    foreach ($name in $buttonNames) { $window.FindName($name).IsEnabled = $Enabled }
    $window.FindName('KnowledgeButton').IsEnabled = ($null -eq $script:ComoKnowledgeWorker)
}

function Invoke-ComoGuiAction([string]$SelectedAction) {
    if ($null -ne $script:ComoActiveWorker) { return }
    $script:ComoFailure = ''
    $statusText.Foreground = [Windows.Media.Brushes]::DarkSlateBlue
    $statusText.Text = '操作を開始しています。'
    $busyBar.Visibility = 'Visible'
    Set-ComoButtons $false
    try { $script:ComoActiveWorker = New-ComoWorker $SelectedAction } catch {
        $script:ComoFailure = '操作を開始できませんでした。もう一度コモ管理を開いてください。'
        $statusText.Text = $script:ComoFailure
        $busyBar.Visibility = 'Collapsed'
        Set-ComoButtons $true
    }
}

$window.FindName('OpenButton').Add_Click({ Invoke-ComoGuiAction 'Open' })
$window.FindName('KnowledgeButton').Add_Click({
    $script:ComoCloseWhenReady = $false
    if ($null -ne $script:ComoActiveWorker) {
        if ($null -eq $script:ComoKnowledgeWorker) {
            try {
                $script:ComoKnowledgeWorker = New-ComoWorker 'Knowledge' -NavigationOnly
                $window.FindName('KnowledgeButton').IsEnabled = $false
            } catch { [Windows.MessageBox]::Show('知識画面を開けませんでした。少し待ってからもう一度お試しください。', 'コモ管理') | Out-Null }
        }
    } else { Invoke-ComoGuiAction 'Knowledge' }
})
$window.FindName('RestartButton').Add_Click({ Invoke-ComoGuiAction 'Restart' })
$window.FindName('StopButton').Add_Click({ Invoke-ComoGuiAction 'Stop' })
$window.FindName('UpdateButton').Add_Click({
    $answer = [Windows.MessageBox]::Show('アプリの更新中はコモが再起動します。利用者が操作していないことを確認してから進めてください。', 'アプリを更新', 'OKCancel', 'Information')
    if ($answer -eq 'OK') { Invoke-ComoGuiAction 'Update' }
})
$window.FindName('LogsButton').Add_Click({
    if (Test-Path -LiteralPath $script:ComoLogPath) { Start-Process notepad.exe -ArgumentList (ConvertTo-ComoArgument $script:ComoLogPath) }
    else { [Windows.MessageBox]::Show('まだ操作記録はありません。', 'コモ管理') | Out-Null }
})

$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(400)
$timer.Add_Tick({
    if ($null -ne $script:ComoKnowledgeWorker) {
        $script:ComoKnowledgeWorker.Refresh()
        if ($script:ComoKnowledgeWorker.HasExited) {
            if ($script:ComoKnowledgeWorker.ExitCode -ne 0) { [Windows.MessageBox]::Show('知識画面を開けませんでした。起動が終わってからもう一度「知識を更新」を押すか、操作記録を確認してください。', 'コモ管理') | Out-Null }
            $script:ComoKnowledgeWorker.Dispose()
            $script:ComoKnowledgeWorker = $null
            $window.FindName('KnowledgeButton').IsEnabled = $true
        }
    }
    if ($script:ComoPendingAction) {
        $selected = $script:ComoPendingAction
        $script:ComoPendingAction = ''
        Invoke-ComoGuiAction $selected
    }
    if ($null -ne $script:ComoActiveWorker) {
        $script:ComoActiveWorker.Refresh()
        $operation = Read-ComoJson $script:ComoOperationPath
        if ($null -ne $operation -and (Get-ComoValue $operation 'process_id' 0) -eq $script:ComoActiveWorker.Id) { $statusText.Text = [string](Get-ComoValue $operation 'message' '処理中です。') }
        if ($script:ComoActiveWorker.HasExited) {
            $exitCode = $script:ComoActiveWorker.ExitCode
            if ($exitCode -ne 0) {
                if ($exitCode -eq 2) { $script:ComoFailure = '別のコモ管理操作が進行中です。少し待ってからもう一度操作してください。' }
                elseif ($null -ne $operation -and (Get-ComoValue $operation 'process_id' 0) -eq $script:ComoActiveWorker.Id) { $script:ComoFailure = [string](Get-ComoValue $operation 'message' '操作が完了しませんでした。操作記録を確認してください。') }
                else { $script:ComoFailure = '操作が完了しませんでした。操作記録を確認してください。' }
            }
            $script:ComoActiveWorker.Dispose()
            $script:ComoActiveWorker = $null
            $busyBar.Visibility = 'Collapsed'
            Set-ComoButtons $true
            $script:ComoLastStatusAt = [DateTime]::MinValue
            if ($script:ComoCloseWhenReady -and $exitCode -eq 0) { $window.Close(); return }
            $script:ComoCloseWhenReady = $false
        }
    }
    if ($null -eq $script:ComoActiveWorker) {
        if ($script:ComoFailure) { $statusText.Text = $script:ComoFailure; $statusText.Foreground = [Windows.Media.Brushes]::Firebrick }
        else {
            $runtime = Read-ComoJson $script:ComoRuntimePath
            if ($null -ne $runtime) {
                $statusText.Text = [string](Get-ComoValue $runtime 'message' '状態を確認しています。')
                $statusText.Foreground = [Windows.Media.Brushes]::DarkSlateBlue
                $lastUpdated = [string](Get-ComoValue $runtime 'last_updated' '')
                if ($lastUpdated) {
                    try { $lastUpdated = [DateTimeOffset]::Parse($lastUpdated).ToLocalTime().ToString('yyyy/MM/dd HH:mm') } catch { }
                    $updatedText.Text = '知識の最終更新: ' + $lastUpdated
                } else { $updatedText.Text = '知識の最終更新は、知識更新画面でも確認できます。' }
            }
        }
        if ($null -ne $script:ComoStatusWorker) {
            $script:ComoStatusWorker.Refresh()
            if ($script:ComoStatusWorker.HasExited) { $script:ComoStatusWorker.Dispose(); $script:ComoStatusWorker = $null }
        }
        if ($null -eq $script:ComoStatusWorker -and ([DateTime]::Now - $script:ComoLastStatusAt).TotalSeconds -ge 15) {
            try { $script:ComoStatusWorker = New-ComoWorker 'Status'; $script:ComoLastStatusAt = [DateTime]::Now } catch { }
        }
    }
})
$window.Add_Closed({ $timer.Stop() })
try { $timer.Start(); [void]$window.ShowDialog() }
finally {
    $timer.Stop()
    if ($null -ne $script:ComoActiveWorker) { $script:ComoActiveWorker.Dispose() }
    if ($null -ne $script:ComoStatusWorker) { $script:ComoStatusWorker.Dispose() }
    if ($null -ne $script:ComoKnowledgeWorker) { $script:ComoKnowledgeWorker.Dispose() }
    if ($guiLocked) { $guiMutex.ReleaseMutex() }
    $guiMutex.Dispose()
}
