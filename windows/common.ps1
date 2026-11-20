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
$script:ComoReleasePath = Join-Path $script:ComoStateDir 'release.json'
$script:ComoSetupPath = Join-Path $script:ComoStateDir 'setup.json'
$script:ComoKioskPath = Join-Path $script:ComoStateDir 'kiosk.json'
$script:ComoStartedAt = [DateTimeOffset]::Now.ToString('o')
$script:ComoStep = ''
$utf8 = New-Object Text.UTF8Encoding($false)
$script:ComoUtf8 = $utf8
$OutputEncoding = $utf8
try { [Console]::OutputEncoding = $utf8 } catch { }

function Get-ComoValue($Object, [string]$Name, $Default = $null) {
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) { return $Object.$Name }
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
    $temporary = $script:ComoEnvPath + '.' + $PID + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, $text, $script:ComoUtf8)
        [IO.File]::Replace($temporary, $script:ComoEnvPath, [NullString]::Value)
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Protect-ComoText([string]$Text) {
    $safe = $Text
    $settings = Get-ComoSettings
    foreach ($key in @($settings.Keys)) {
        if ($key -match '(KEY|TOKEN|PASSWORD|SECRET|DSN)' -and $settings[$key].Length -ge 4) { $safe = $safe.Replace($settings[$key], '[非表示]') }
    }
    $safe = [regex]::Replace($safe, '(?i)(https?://)[^/@\s]+@', '$1[非表示]@')
    $safe = [regex]::Replace($safe, '(?i)(AIza[A-Za-z0-9_-]{20,}|(postgres(?:ql)?://)[^\s"'']+)', '[非表示]')
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

function Write-ComoOperation([string]$Phase, [string]$Message, [string]$Operation, [string]$Code = '', [int]$Progress = -1) {
    Write-ComoJson $script:ComoOperationPath @{
        phase = $Phase; message = (Protect-ComoText $Message); operation = $Operation; code = $Code
        updated_at = [DateTimeOffset]::Now.ToString('o'); started_at = $script:ComoStartedAt
        step = $script:ComoStep; progress = $Progress; process_id = $PID
    }
}

function Write-ComoStep([string]$Operation, [string]$Step, [string]$Message, [int]$Progress = -1) {
    $script:ComoStep = $Step
    Write-ComoOperation 'running' $Message $Operation '' $Progress
}

function New-ComoError([string]$Message, [string]$Code, [int]$HttpStatus = 0) {
    $errorValue = [InvalidOperationException]::new($Message)
    $errorValue.Data['como_code'] = $Code
    $errorValue.Data['http_status'] = $HttpStatus
    return $errorValue
}

function Get-ComoFailureNotice($Failure) {
    $message = [string](Get-ComoValue $Failure 'message' '')
    if (-not $message) { return '' }
    $operation = Get-ComoValue $Failure 'operation' ''
    $notice = switch (Get-ComoValue $Failure 'code' '') {
        'gemini_key' { 'コモをつなぐためのコードを使えませんでした。担当者から受け取ったコードを確かめてください。' }
        'gemini_limit' { '質問に答えるために使える量が上限に達しました。時間をおいて、もう一度試してください。' }
        'gemini_model' { '今の設定では、コモが質問に答えられません。設置を担当した人に記録を渡してください。' }
        'network' { '質問に答えるための通信ができませんでした。パソコンがインターネットにつながっているか確かめてください。' }
        'settings' { 'コモを開くための設定を確かめる必要があります。「最初の設定を見直す」を押してください。' }
        'software' { 'このパソコンで使い始めるための準備が残っています。設置を担当した人に記録を渡してください。' }
        'docker' { 'コモを開くための準備が終わりませんでした。少し待って、もう一度試してください。' }
        'port' { '別のアプリが動いているため、コモを開けませんでした。設置を担当した人に記録を渡してください。' }
        'git' { 'コモの更新を確かめられませんでした。インターネットを確かめ、続くときは担当者に記録を渡してください。' }
        'database' { 'コモが使う案内を読み出せませんでした。「もう一度試す」を押してください。' }
        'storage' { '案内をこのパソコンに保存できませんでした。設置を担当した人に記録を渡してください。' }
        'busy' { '案内を変えている途中です。「案内を更新する」で終わったことを確かめてから、もう一度試してください。' }
        'knowledge_wait' { '案内の準備が続いています。「案内を更新する」で様子を確かめられます。' }
        'browser' {
            if ($operation -eq 'Stop') { 'コモの画面を閉じられませんでした。コモの画面を閉じてから「今日の利用を終える」を押してください。' }
            else { 'コモの画面を開いたことを確かめられませんでした。画面の下にコモのアイコンがないか確かめてください。' }
        }
        'release' {
            if ($operation -eq 'Rollback') { 'ひとつ前のコモを開けませんでした。「コモ本体を更新する」で今届いているコモを用意できます。' }
            else { '選んだコモを開けませんでした。「コモ本体を更新する」から、もう一度試せます。' }
        }
        default { '準備を最後まで終えられませんでした。少し待って、もう一度試してください。' }
    }
    if ($message -match '前の(?:版|コモ)に戻しました') {
        return ('更新が終わらなかったため、前のコモに戻しました。' + $notice)
    }
    return $notice
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
    $names = @{ git = 'Git'; docker = 'Docker Desktop'; desktop = 'Docker Desktop'; edge = 'Microsoft Edge' }
    throw (New-ComoError ($names[$Kind] + 'が見つかりません。設置担当者にインストールを依頼してください。') 'software')
}

function ConvertTo-ComoArgument([string]$Value) {
    # Windows file names cannot contain quotes. Refuse them rather than invoking a shell.
    if ($Value.Contains('"')) { throw '引数に使用できない文字があります。' }
    return '"' + $Value.TrimEnd('\') + '"'
}

function Invoke-ComoProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 1800, [int[]]$AllowedExitCodes = @(0), [hashtable]$Environment = @{}, [switch]$Quiet) {
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
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = $Environment[$key] }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw (New-ComoError '準備が時間内に終わりませんでした。インターネットにつながっているか確かめてください。' 'timed_out')
        }
        $result = @{ exit_code = $process.ExitCode; output = $stdout.Result; error = $stderr.Result }
        if (-not $Quiet) {
            foreach ($output in @($result.output, $result.error)) {
                if ($output.Trim()) { Write-ComoLog $output.Trim() }
            }
        }
        if ($AllowedExitCodes -notcontains $process.ExitCode) {
            $message = '準備を最後まで終えられませんでした。コモ管理からもう一度試せます。'
            $code = 'command'
            if ($result.error -match '(?i)port is already allocated|address already in use|ports are not available') {
                $message = '必要なポートを別のアプリが使っています。開発担当者に起動中のアプリを確認してもらってください。'
                $code = 'port'
            }
            throw (New-ComoError $message $code)
        }
        return $result
    } finally { $process.Dispose() }
}

function Invoke-ComoCompose([string[]]$Arguments, [string]$Revision = '', [switch]$Quiet) {
    $composeArgs = @('compose', '--project-directory', $script:ComoRoot, '-f', (Join-Path $script:ComoRoot 'docker-compose.yml'))
    $processEnv = @{}
    if ($Revision) {
        $composeArgs += @('-f', (Join-Path $PSScriptRoot 'internal\compose.windows.yml'))
        $processEnv = @{ COMO_WINDOWS_IMAGE = (Get-ComoImage $Revision); COMO_WINDOWS_REVISION = $Revision }
    }
    return (Invoke-ComoProcess (Get-ComoExecutable 'docker') ($composeArgs + $Arguments) -Environment $processEnv -Quiet:$Quiet)
}

function Assert-ComoSettings([switch]$AllowMissingGemini) {
    $settings = Get-ComoSettings
    if (-not $settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY') -or $settings.CHAT_LOG_ADMIN_API_KEY -match '^(|change-this-admin-key)$') {
        throw (New-ComoError '初回設定で管理用の接続を準備してください。' 'settings')
    }
    if (-not $AllowMissingGemini -and (-not $settings.ContainsKey('API_KEY') -or $settings.API_KEY -match '^(|your-gemini-api-key)$')) {
        throw (New-ComoError 'Geminiの接続情報が未設定です。「初回設定・接続を直す」から設定できます。' 'gemini_key')
    }
}

function Wait-ComoDocker {
    $docker = Get-ComoExecutable 'docker'
    $check = Invoke-ComoProcess $docker @('info', '--format', '{{.ServerVersion}}') 15 @(0, 1) -Quiet
    if ($check.exit_code -eq 0) { return }
    Start-Process -FilePath (Get-ComoExecutable 'desktop') | Out-Null
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        Start-Sleep -Seconds 3
        $check = Invoke-ComoProcess $docker @('info', '--format', '{{.ServerVersion}}') 15 @(0, 1) -Quiet
        if ($check.exit_code -eq 0) { return }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw (New-ComoError 'Docker Desktopの準備ができません。Docker Desktopを開き、表示を確認してから再試行してください。' 'docker')
}

function Invoke-ComoApi([string]$Path, [string]$Method = 'GET', [int]$TimeoutSeconds = 10) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), ($script:ComoBaseUrl + $Path))
    $response = $null
    try {
        if ($Path.StartsWith('/api/admin/')) {
            $settings = Get-ComoSettings
            if ($settings.ContainsKey('CHAT_LOG_ADMIN_API_KEY')) {
                [void]$request.Headers.TryAddWithoutValidation('X-Admin-Key', $settings.CHAT_LOG_ADMIN_API_KEY)
            }
        }
        try {
            $response = $client.SendAsync($request).GetAwaiter().GetResult()
            $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
        } catch {
            throw (New-ComoError '設置PCのコモから応答がありません。少し待って再試行してください。' 'local_connection')
        }
        $payload = $null
        # Decode the bytes explicitly, including error responses, on PowerShell 5.1.
        try { $payload = $script:ComoUtf8.GetString($bytes) | ConvertFrom-Json } catch { }
        if ($response.IsSuccessStatusCode -and $null -ne $payload) { return $payload }

        $status = [int]$response.StatusCode
        $detail = Get-ComoValue $payload 'detail'
        $code = [string](Get-ComoValue $detail 'code' '')
        if ($code -match '^(gemini_key|gemini_limit|gemini_model|gemini_service|network|database|storage|interrupted|unknown)$') {
            throw (New-ComoError ([string](Get-ComoValue $detail 'message' '処理が失敗しました。')) $code $status)
        }
        if ($status -in @(401, 403)) {
            throw (New-ComoError '管理用の接続設定が一致しません。「初回設定・接続を直す」から確認してください。' 'settings' $status)
        }
        if ($status -eq 503 -and $Path.StartsWith('/api/admin/knowledge')) {
            throw (New-ComoError '知識の保存先を利用できません。「再起動」を試し、続く場合は担当者へ診断記録を渡してください。' 'database' $status)
        }
        throw (New-ComoError ('コモからの応答を確認できません（HTTP ' + $status + '）。再試行してください。') 'local_connection' $status)
    } finally {
        if ($null -ne $response) { $response.Dispose() }
        $request.Dispose()
        $client.Dispose()
    }
}

function Get-ComoHealth {
    try {
        $health = Invoke-ComoApi '/api/health' 'GET' 2
        if ((Get-ComoValue $health 'status') -eq 'ok') { return $health }
    } catch { }
    return $null
}

function Wait-ComoHealth([string]$Revision = '', [int]$Seconds = 180) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $health = Get-ComoHealth
        if ($null -ne $health -and (-not $Revision -or $Revision -eq 'legacy' -or (Get-ComoValue $health 'windows_revision') -eq $Revision)) {
            return $health
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw (New-ComoError '用意した版のコモに接続できませんでした。「再試行」を押すか、担当者へ診断記録を渡してください。' 'local_connection')
}

function Get-ComoKnowledgeState {
    try { return (Invoke-ComoApi '/api/admin/knowledge/readiness' 'GET' 5) }
    catch {
        if ($_.Exception.Data['http_status'] -eq 404) { return (Invoke-ComoApi '/api/admin/knowledge/status' 'GET' 5) }
        throw
    }
}

function Wait-ComoKnowledge([string]$Operation) {
    $deadline = [DateTime]::UtcNow.AddMinutes(40)
    $state = Get-ComoKnowledgeState
    $jobId = ''
    while (Get-ComoValue $state 'initialization_required' $true) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw (New-ComoError '案内の準備が続いています。「案内を更新する」で様子を確かめられます。' 'knowledge_wait')
        }
        $jobId = [string](Get-ComoValue $state 'active_job' '')
        if ($jobId) { break }
        if (Get-ComoValue $state 'busy' $false) {
            Write-ComoStep $Operation '案内を準備する' 'コモが案内を使えるようになるまでお待ちください。'
        } else {
            Write-ComoStep $Operation '案内を準備する' 'コモが最初の案内を使えるように準備しています。'
            try {
                $started = Invoke-ComoApi '/api/admin/knowledge/initialize' 'POST' 30
                $jobId = [string](Get-ComoValue $started 'job_id' '')
                if (-not $jobId) { throw (New-ComoError '案内の準備を始められませんでした。「案内を更新する」で様子を確かめてください。' 'knowledge') }
                break
            } catch {
                if ($_.Exception.Data['http_status'] -ne 409) { throw }
            }
        }
        Start-Sleep -Seconds 2
        $state = Get-ComoKnowledgeState
    }
    if (-not $jobId) { return $state }
    do {
        Start-Sleep -Seconds 2
        $job = Invoke-ComoApi ('/api/admin/knowledge/jobs/' + [Uri]::EscapeDataString($jobId))
        $status = [string](Get-ComoValue $job 'status' '')
        $message = [string](Get-ComoValue $job 'message' 'コモが案内を使えるように準備しています。')
        if ($status -eq 'succeeded') {
            $state = Get-ComoKnowledgeState
            if (-not (Get-ComoValue $state 'initialization_required' $true)) { return $state }
            throw (New-ComoError '案内の準備が終わったか分かりません。「案内を更新する」で様子を確かめてください。' 'knowledge')
        }
        if ($status -eq 'failed') {
            throw (New-ComoError $message ([string](Get-ComoValue $job 'error_code' 'knowledge')))
        }
        Write-ComoStep $Operation '案内を準備する' $message ([int](Get-ComoValue $job 'progress' -1))
    } while ([DateTime]::UtcNow -lt $deadline)
    throw (New-ComoError '案内の準備が続いています。「案内を更新する」で様子を確かめられます。' 'knowledge_wait')
}

function Assert-ComoKnowledgeIdle {
    try { $state = Get-ComoKnowledgeState } catch { return }
    if (Get-ComoValue $state 'busy' $false) {
        throw (New-ComoError '案内を変えている途中です。「案内を更新する」で終わったことを確かめてから、もう一度試してください。' 'busy')
    }
}

function Set-ComoKioskPaused([bool]$Paused) {
    Write-ComoJson $script:ComoKioskPath @{ chat_paused = $Paused }
}

function Update-ComoRuntime {
    $health = Get-ComoHealth
    $operation = Read-ComoJson $script:ComoOperationPath
    $revision = [string](Get-ComoValue $health 'windows_revision' '')
    $release = Read-ComoJson $script:ComoReleasePath
    $setup = Read-ComoJson $script:ComoSetupPath
    $state = $null
    $paused = [bool](Get-ComoValue $health 'kiosk_chat_paused' $false)
    $message = 'コモはまだ開いていません。「コモを開く」を押してください。'
    if ($null -ne $health) {
        try { $state = Get-ComoKnowledgeState } catch { }
        if ($paused) {
            $message = 'コモの画面を開けます。いまは質問への回答をお休みしています。'
        } elseif ($null -eq $state) {
            $message = 'コモの画面を開けます。質問に答える準備ができたか分かりません。'
        } elseif (Get-ComoValue $state 'initialization_required' $true) {
            $message = 'コモが質問に答えるには、案内の準備が必要です。'
        } else {
            $message = 'コモを使えます。'
        }
    } elseif ((Get-ComoValue $operation 'operation') -eq 'Stop' -and (Get-ComoValue $operation 'phase') -eq 'succeeded') {
        $message = '今日の利用を終えました。パソコンの電源を切って大丈夫です。'
    }
    $runningRevision = ''
    if ($null -ne $health) { $runningRevision = if ($revision) { $revision } else { '旧版' } }
    Write-ComoJson $script:ComoRuntimePath @{
        message = $message; server_up = ($null -ne $health); chat_paused = $paused
        initialized = (Get-ComoValue $state 'initialized' $false)
        last_updated = (Get-ComoValue $state 'last_updated')
        running_revision = $runningRevision
        installed_revision = (Get-ComoHead -AllowUnavailable)
        previous_revision = (Get-ComoValue $release 'previous' '')
        failed_revision = (Get-ComoValue $release 'failed' '')
        auto_start = (Get-ComoValue $setup 'auto_start' '')
        checked_at = [DateTimeOffset]::Now.ToString('o')
    }
}
