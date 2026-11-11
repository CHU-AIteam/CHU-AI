#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ComoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:ComoEnvPath = Join-Path $script:ComoRoot 'src\.env'
$script:ComoBaseUrl = 'http://127.0.0.1:8000'
$script:ComoStateDir = Join-Path $script:ComoRoot 'data\windows-launcher'
$script:ComoOperationPath = Join-Path $script:ComoStateDir 'operation.json'
$script:ComoRuntimePath = Join-Path $script:ComoStateDir 'runtime.json'
$script:ComoLogPath = Join-Path $script:ComoStateDir 'operations.log'
$utf8 = New-Object Text.UTF8Encoding($false)
$script:ComoUtf8 = $utf8
$OutputEncoding = $utf8
try { [Console]::OutputEncoding = $utf8 } catch { }

function Get-ComoValue($Object, [string]$Name, $Default = $null) {
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}

function Get-ComoSettings {
    $values = @{}
    if (-not (Test-Path -LiteralPath $script:ComoEnvPath)) { return $values }
    foreach ($line in [IO.File]::ReadAllLines($script:ComoEnvPath, $script:ComoUtf8)) {
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name, $value = $matches[1], $matches[2].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            } else { $value = ($value -split '\s+#', 2)[0].Trim() }
            $values[$name] = $value
        }
    }
    return $values
}

function Set-ComoSetting([string]$Name, [string]$Value) {
    if ($Name -notmatch '^[A-Z][A-Z0-9_]*$' -or $Value -match '[\r\n]') { throw '設定値を確認してください。' }
    $text = [IO.File]::ReadAllText($script:ComoEnvPath, $script:ComoUtf8)
    $pattern = '(?m)^[ \t]*(?:export[ \t]+)?' + [regex]::Escape($Name) + '[ \t]*=[^\r\n]*'
    if ([regex]::IsMatch($text, $pattern)) {
        $replacement = $Name + '=' + $Value
        $text = [regex]::Replace($text, $pattern, [Text.RegularExpressions.MatchEvaluator]{ param($match) $replacement })
    } else { $text = $text.TrimEnd() + [Environment]::NewLine + $Name + '=' + $Value + [Environment]::NewLine }
    [IO.File]::WriteAllText($script:ComoEnvPath, $text, $script:ComoUtf8)
}

function Protect-ComoText([string]$Text) {
    $safe = $Text
    $settings = Get-ComoSettings
    foreach ($key in @($settings.Keys)) {
        if ($key -match '(KEY|TOKEN|PASSWORD|SECRET|DSN)' -and $settings[$key].Length -ge 4) { $safe = $safe.Replace($settings[$key], '[非表示]') }
    }
    $safe = [regex]::Replace($safe, '(?i)(https?://)[^/@\s]+@', '$1[非表示]@')
    return [regex]::Replace($safe, '(?i)#key=[^\s"'']+', '#key=[非表示]')
}

function Write-ComoLog([string]$Message) {
    [IO.Directory]::CreateDirectory($script:ComoStateDir) | Out-Null
    $line = [DateTimeOffset]::Now.ToString('o') + ' ' + (Protect-ComoText $Message) + [Environment]::NewLine
    [IO.File]::AppendAllText($script:ComoLogPath, $line, $script:ComoUtf8)
}

function Write-ComoJson([string]$Path, $Value) {
    [IO.Directory]::CreateDirectory($script:ComoStateDir) | Out-Null
    $temporary = $Path + '.' + $PID + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8), $script:ComoUtf8)
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        try {
            if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) } else { [IO.File]::Move($temporary, $Path) }
            return
        } catch [IO.IOException] {
            if ($attempt -eq 9) { throw }
            Start-Sleep -Milliseconds 50
        }
    }
}

function Read-ComoJson([string]$Path) {
    $reader = $null
    $stream = $null
    try {
        # FileShare.Delete permits the worker to atomically replace its snapshot
        # while the WPF timer reads it on Windows.
        $sharing = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
        $reader = New-Object IO.StreamReader($stream, $script:ComoUtf8, $true)
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } catch { return $null }
    finally {
        if ($null -ne $reader) { $reader.Dispose() } elseif ($null -ne $stream) { $stream.Dispose() }
    }
}

function Write-ComoOperation([string]$Phase, [string]$Message, [string]$Operation) {
    Write-ComoJson $script:ComoOperationPath @{ phase = $Phase; message = (Protect-ComoText $Message); operation = $Operation; updated_at = [DateTimeOffset]::Now.ToString('o'); process_id = $PID }
}

function Get-ComoId {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($script:ComoUtf8.GetBytes($script:ComoRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 12)) } finally { $hash.Dispose() }
}

function Get-ComoPowerShell { return (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') }

function Get-ComoExecutable([string]$Kind) {
    $candidates = @()
    switch ($Kind) {
        'docker' { $candidates = @((Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')) }
        'git' { $candidates = @((Join-Path $env:ProgramFiles 'Git\cmd\git.exe')) }
        'edge' {
            $candidates = @((Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'), (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'), (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe'))
        }
        'desktop' { $candidates = @((Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')) }
    }
    if ($Kind -in @('docker', 'git')) {
        $command = Get-Command ($Kind + '.exe') -ErrorAction SilentlyContinue
        if ($null -ne $command) { return $command.Source }
    }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    throw ('必要なソフトが見つかりません: ' + $Kind + '。初回設定の手順を確認してください。')
}

function ConvertTo-ComoArgument([string]$Value) {
    # Windows file names cannot contain quotes. Refuse them rather than invoking a shell.
    if ($Value.Contains('"')) { throw '引数に使用できない文字があります。' }
    return '"' + $Value.TrimEnd('\') + '"'
}

function Invoke-ComoProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 1800, [int[]]$AllowedExitCodes = @(0)) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-ComoArgument $_ }) -join ' ')
    $info.WorkingDirectory = $script:ComoRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $script:ComoUtf8
    $info.StandardErrorEncoding = $script:ComoUtf8
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw '処理が時間内に終わりませんでした。ネットワーク接続とDocker Desktopを確認してください。'
        }
        $result = @{ exit_code = $process.ExitCode; output = $stdout.Result; error = $stderr.Result }
        foreach ($output in @($result.output, $result.error)) { if ($output.Trim()) { Write-ComoLog $output.Trim() } }
        if ($AllowedExitCodes -notcontains $process.ExitCode) {
            throw ('処理に失敗しました。操作記録を確認してください。' + [Environment]::NewLine + (Protect-ComoText $result.error.Trim()))
        }
        return $result
    } finally { $process.Dispose() }
}

function Invoke-ComoCompose([string[]]$Arguments) {
    return (Invoke-ComoProcess (Get-ComoExecutable 'docker') (@('compose', '--project-directory', $script:ComoRoot) + $Arguments))
}

function Assert-ComoSettings {
    $settings = Get-ComoSettings
    if (-not $settings.ContainsKey('API_KEY') -or $settings.API_KEY -match '^(|your-gemini-api-key)$') { throw '初回設定が必要です。windows\setup.cmd を開き、APIキーを設定してください。' }
    if (-not $settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY') -or $settings.CHAT_LOG_ADMIN_API_KEY -match '^(|change-this-admin-key)$') { throw '管理画面の初回設定が必要です。windows\setup.cmd を開いてください。' }
}

function Wait-ComoDocker {
    $docker = Get-ComoExecutable 'docker'
    $check = Invoke-ComoProcess $docker @('info', '--format', '{{.ServerVersion}}') 15 @(0, 1)
    if ($check.exit_code -eq 0) { return }
    Start-Process -FilePath (Get-ComoExecutable 'desktop') | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        Start-Sleep -Seconds 3
        $check = Invoke-ComoProcess $docker @('info', '--format', '{{.ServerVersion}}') 15 @(0, 1)
        if ($check.exit_code -eq 0) { return }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Docker Desktopの準備が終わりませんでした。Docker Desktopを開いて表示を確認してから、もう一度「コモを開く」を押してください。'
}

function Invoke-ComoApi([string]$Path, [string]$Method = 'GET', [int]$TimeoutSeconds = 10) {
    $settings = Get-ComoSettings
    $headers = @{}
    if ($settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY')) { $headers['X-Admin-Key'] = $settings.CHAT_LOG_ADMIN_API_KEY }
    try { return (Invoke-RestMethod -Uri ($script:ComoBaseUrl + $Path) -Method $Method -Headers $headers -TimeoutSec $TimeoutSeconds -UseBasicParsing) }
    catch {
        $response = Get-ComoValue $_.Exception 'Response'
        $code = Get-ComoValue $response 'StatusCode'
        if ($null -ne $code -and [int]$code -eq 401) { throw '管理画面の接続設定が一致しません。「コモを再起動」を実行し、改善しない場合は初回設定をした担当者へ連絡してください。' }
        throw
    }
}

function Wait-ComoHealth {
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    $healthReady = $false
    do {
        try {
            $health = Invoke-ComoApi '/api/health' 'GET' 3
            if ((Get-ComoValue $health 'status') -eq 'ok') { $healthReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $healthReady) { throw 'コモに接続できません。操作記録を開いて起動状況を確認してください。' }
    return $health
}

function Wait-ComoKnowledge([string]$Operation) {
    Wait-ComoHealth | Out-Null
    $state = Invoke-ComoApi '/api/admin/knowledge/status'
    $deadline = [DateTime]::UtcNow.AddMinutes(40)
    $jobId = ''
    while (Get-ComoValue $state 'initialization_required' $true) {
        if ($null -eq (Get-ComoValue $state 'initialization_required')) { throw '管理画面の更新が必要です。「アプリを更新」を実行してください。' }
        if ([DateTime]::UtcNow -ge $deadline) { throw '知識の準備に時間がかかっています。「知識を更新」で現在の状態を確認してください。' }
        $jobId = [string](Get-ComoValue $state 'active_job' '')
        if ($jobId) {
            Write-ComoOperation 'running' '進行中の知識の準備を確認しています。「知識を更新」で管理画面も開けます。' $Operation
            break
        }
        if (Get-ComoValue $state 'busy' $false) {
            Write-ComoOperation 'running' '知識の更新処理が始まっています。準備ができるまでお待ちください。' $Operation
            Start-Sleep -Seconds 2
            $state = Invoke-ComoApi '/api/admin/knowledge/status'
            continue
        }
        Write-ComoOperation 'running' '初回の知識を準備しています。数分かかることがあります。' $Operation
        try {
            $started = Invoke-ComoApi '/api/admin/knowledge/initialize' 'POST' 30
            $jobId = [string](Get-ComoValue $started 'job_id' '')
            if (-not $jobId) { throw '知識の準備を開始できませんでした。管理画面の状態を確認してください。' }
            break
        } catch {
            $startError = $_
            # Another screen may have started the job after the status check.
            # Rejoin that job instead of repeatedly posting initialize (409).
            $state = Invoke-ComoApi '/api/admin/knowledge/status'
            if ((Get-ComoValue $state 'active_job' '') -or (Get-ComoValue $state 'busy' $false) -or -not (Get-ComoValue $state 'initialization_required' $true)) { continue }
            throw $startError
        }
    }
    if ($jobId) {
        $completed = $false
        do {
            Start-Sleep -Seconds 2
            $job = Invoke-ComoApi ('/api/admin/knowledge/jobs/' + [Uri]::EscapeDataString($jobId))
            $status = [string](Get-ComoValue $job 'status' '')
            if ($status -eq 'succeeded') { $completed = $true; break }
            if ($status -eq 'failed') {
                $details = [string](Get-ComoValue $job 'error' '')
                if (-not $details) { $details = [string](Get-ComoValue $job 'message' '管理画面の案内を確認してください。') }
                throw ('知識の準備が完了しませんでした。「知識を更新」で状態を確認・再実行できます。' + [Environment]::NewLine + $details)
            }
            $message = [string](Get-ComoValue $job 'message' '知識の検索データを準備しています。')
            Write-ComoOperation 'running' $message $Operation
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $completed) { throw '知識の準備に時間がかかっています。「知識を更新」で現在の状態を確認してください。' }
    }
    $state = Invoke-ComoApi '/api/admin/knowledge/status'
    if (Get-ComoValue $state 'initialization_required' $true) { throw '知識の準備を確認できませんでした。「知識を更新」で状態を確認してください。' }
    return $state
}

function Start-ComoServices([string]$Operation, [switch]$SkipKnowledgePreparation) {
    Assert-ComoSettings
    if ($SkipKnowledgePreparation) {
        try {
            $health = Invoke-ComoApi '/api/health' 'GET' 3
            if ((Get-ComoValue $health 'status') -eq 'ok') { return $health }
        } catch { }
    }
    Write-ComoOperation 'running' 'Docker Desktopの準備を待っています。' $Operation
    Wait-ComoDocker
    Write-ComoOperation 'running' 'コモを起動しています。初回や更新後は数分かかることがあります。' $Operation
    Invoke-ComoCompose @('up', '-d', '--build') | Out-Null
    if ($SkipKnowledgePreparation) {
        Write-ComoOperation 'running' '知識の管理画面を開く準備をしています。' $Operation
        return (Wait-ComoHealth)
    }
    Write-ComoOperation 'running' 'コモと知識の準備を確認しています。' $Operation
    return (Wait-ComoKnowledge $Operation)
}

function Assert-ComoKnowledgeIdle {
    try { $state = Invoke-ComoApi '/api/admin/knowledge/status' 'GET' 3 }
    catch { return } # An unavailable backend must still be recoverable.
    if (Get-ComoValue $state 'busy' $false) { throw '知識の更新が終わってから操作してください。「知識を更新」で現在の状態を確認できます。' }
}

function Open-ComoBrowser([switch]$Knowledge) {
    $edge = Get-ComoExecutable 'edge'
    if ($Knowledge) {
        $settings = Get-ComoSettings
        $url = $script:ComoBaseUrl + '/manage/#key=' + [Uri]::EscapeDataString($settings.CHAT_LOG_ADMIN_API_KEY)
        $arguments = @('--new-window', ('--app=' + $url))
    } else {
        $arguments = @('--new-window', '--start-fullscreen', ($script:ComoBaseUrl + '/?kiosk=1'))
    }
    $joined = (($arguments | ForEach-Object { ConvertTo-ComoArgument $_ }) -join ' ')
    Start-Process -FilePath $edge -ArgumentList $joined | Out-Null
}

function Update-ComoCode {
    $git = Get-ComoExecutable 'git'
    $dirty = Invoke-ComoProcess $git @('status', '--porcelain', '--untracked-files=no') 30
    if ($dirty.output.Trim()) { throw 'このPCに編集中のプログラムがあります。担当者へ連絡して内容を確認してください。知識更新には「知識を更新」を使ってください。' }
    $tracking = Invoke-ComoProcess $git @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') 30
    if (-not $tracking.output.Trim()) { throw 'Gitの更新先が未設定です。初回設定をした担当者へ連絡してください。' }
    $ahead = Invoke-ComoProcess $git @('rev-list', '--count', '@{u}..HEAD') 30
    if ([int]$ahead.output.Trim() -gt 0) { throw '本番PCに未反映のコミットがあります。更新前に開発担当者へ連絡してください。' }
    Write-ComoOperation 'running' 'Gitからアプリの更新を取得しています。' 'Update'
    Invoke-ComoProcess $git @('pull', '--ff-only') 300 | Out-Null
}

function Update-ComoRuntime {
    $available = $false
    $lastUpdated = $null
    $message = '停止中です。「コモを開く」で利用を始められます。'
    try {
        $health = Invoke-ComoApi '/api/health' 'GET' 2
        $state = Invoke-ComoApi '/api/admin/knowledge/status' 'GET' 3
        $available = ((Get-ComoValue $health 'status') -eq 'ok') -and -not (Get-ComoValue $state 'initialization_required' $true)
        if ($available) { $message = '利用できます。' } else { $message = 'コモは起動しています。知識の準備を確認してください。' }
        $lastUpdated = Get-ComoValue $state 'last_updated'
    } catch { }
    Write-ComoJson $script:ComoRuntimePath @{ available = $available; message = $message; last_updated = $lastUpdated; checked_at = [DateTimeOffset]::Now.ToString('o') }
}
