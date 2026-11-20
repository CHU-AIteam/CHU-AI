function Get-ComoHead([switch]$AllowUnavailable) {
    try {
        $git = Get-ComoExecutable 'git'
        $result = Invoke-ComoProcess $git @('rev-parse', '--short=12', 'HEAD') 20 -Quiet
        $revision = $result.output.Trim().ToLowerInvariant()
        if ($revision -notmatch '^[0-9a-f]{12}$') { throw 'Gitの版を確認できません。' }
        return $revision
    } catch {
        if ($AllowUnavailable) { return '' }
        throw (New-ComoError 'Gitから取得したCHU-AIフォルダで初回設定を実行してください。' 'git')
    }
}

function Get-ComoImage([string]$Revision) {
    if ($Revision -notmatch '^([0-9a-f]{12}|legacy)$') { throw '選んだアプリの版を確認できません。' }
    return ('como-local/chu-ai-' + (Get-ComoId).ToLowerInvariant() + ':' + $Revision)
}

function Test-ComoImage([string]$Revision) {
    if (-not $Revision) { return $false }
    $check = Invoke-ComoProcess (Get-ComoExecutable 'docker') @('image', 'inspect', '--format', '{{.Id}}', (Get-ComoImage $Revision)) 20 @(0, 1) -Quiet
    return ($check.exit_code -eq 0)
}

function Save-ComoLegacyImage {
    $container = (Invoke-ComoCompose @('ps', '--all', '--quiet', 'chu-ai') -Quiet).output.Trim()
    if ($container -notmatch '^[a-f0-9]{12,64}$') { return '' }
    $image = (Invoke-ComoProcess (Get-ComoExecutable 'docker') @('inspect', '--format', '{{.Image}}', $container) 20 -Quiet).output.Trim()
    if ($image -notmatch '^sha256:[a-f0-9]{64}$') { return '' }
    Invoke-ComoProcess (Get-ComoExecutable 'docker') @('image', 'tag', $image, (Get-ComoImage 'legacy')) 30 | Out-Null
    return 'legacy'
}

function Update-ComoCode {
    $git = Get-ComoExecutable 'git'
    $dirty = Invoke-ComoProcess $git @('status', '--porcelain', '--untracked-files=no') 30 -Quiet
    if ($dirty.output.Trim()) { throw (New-ComoError 'このPCに編集されたプログラムがあります。更新前に開発担当者に確認してください。' 'git') }
    $tracking = Invoke-ComoProcess $git @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') 30 @(0, 128) -Quiet
    if ($tracking.exit_code -ne 0 -or -not $tracking.output.Trim()) {
        throw (New-ComoError 'アプリの取得先が未設定です。初回設定をした担当者に確認してください。' 'git')
    }
    $ahead = Invoke-ComoProcess $git @('rev-list', '--count', '@{u}..HEAD') 30 -Quiet
    if ([int]$ahead.output.Trim() -gt 0) { throw (New-ComoError 'このPCに未配布のコミットがあります。開発担当者に確認してください。' 'git') }
    try {
        Invoke-ComoProcess $git @('pull', '--ff-only') 300 -Environment @{ GIT_TERMINAL_PROMPT = '0' } | Out-Null
    } catch {
        throw (New-ComoError '配布版を取得できません。ネット接続を確認し、続く場合は担当者にGitの取得先と認証を確認してください。' 'git')
    }
}

function Check-ComoUpdate {
    $git = Get-ComoExecutable 'git'
    try {
        $tracking = Invoke-ComoProcess $git @('rev-parse', '--short=12', '@{u}') 25 -Quiet
        Invoke-ComoProcess $git @('fetch', '--quiet') 90 -Environment @{ GIT_TERMINAL_PROMPT = '0' } | Out-Null
        $tracking = Invoke-ComoProcess $git @('rev-parse', '--short=12', '@{u}') 25 -Quiet
        $counts = (Invoke-ComoProcess $git @('rev-list', '--left-right', '--count', 'HEAD...@{u}') 25 -Quiet).output.Trim() -split '\s+'
        if ([int]$counts[0] -gt 0) { return 'このPCに配布版にない変更があります。開発担当者に確認してください。' }
        if ([int]$counts[1] -gt 0) {
            return ('新しい配布版 ' + $tracking.output.Trim() + ' があります。「アプリを更新」で反映できます。')
        }
        $release = Read-ComoJson $script:ComoReleasePath
        if ((Get-ComoValue $release 'failed') -or ((Get-ComoValue $release 'current') -and (Get-ComoValue $release 'current') -ne (Get-ComoHead))) {
            return '取得済みの版をまだ利用していません。「アプリを更新」から適用できます。'
        }
        return '取得済みの版が最新です。'
    } catch {
        throw (New-ComoError '更新の有無を確認できません。ネット接続とGitの取得先を確認してください。' 'git')
    }
}

function Start-ComoApplication(
    [string]$Operation,
    [switch]$HealthOnly,
    [switch]$CheckChat,
    [switch]$ApplyCheckout,
    [switch]$Restart,
    [switch]$Rollback
) {
    $health = Get-ComoHealth
    if ($HealthOnly -and $null -ne $health) { return $health }
    Assert-ComoSettings -AllowMissingGemini:$HealthOnly
    $checkout = Get-ComoHead
    $release = Read-ComoJson $script:ComoReleasePath
    $current = [string](Get-ComoValue $release 'current' '')
    $previous = [string](Get-ComoValue $release 'previous' '')
    $failed = [string](Get-ComoValue $release 'failed' '')
    $candidate = $current
    if ($Rollback) {
        if (-not $previous) { throw (New-ComoError '戻せるアプリの版がありません。' 'release') }
        $candidate = $previous
    } elseif ($ApplyCheckout -or -not $current -or $checkout -ne (Get-ComoValue $release 'checkout')) {
        $candidate = $checkout
    }

    $envHash = (Get-FileHash -LiteralPath $script:ComoEnvPath -Algorithm SHA256).Hash
    $runningRevision = [string](Get-ComoValue $health 'windows_revision' '')
    $sameVersion = $candidate -eq $runningRevision -or ($candidate -eq 'legacy' -and -not $runningRevision)
    $matching = ($null -ne $health -and $sameVersion -and
        $envHash -eq (Get-ComoValue $release 'env_hash') -and -not $Restart)
    $switched = $false
    $checkingReadiness = $false
    $fallback = $current
    $pauseWasOn = [bool](Get-ComoValue (Read-ComoJson $script:ComoKioskPath) 'chat_paused' $false)
    try {
        if (-not $matching) {
            Assert-ComoKnowledgeIdle
            Write-ComoStep $Operation '起動の準備' 'Docker Desktopの準備を待っています。'
            Wait-ComoDocker
            if (-not $fallback -and $null -ne $health -and -not (Get-ComoValue $health 'windows_revision')) {
                try {
                    $oldState = Get-ComoKnowledgeState
                    if (Get-ComoValue $oldState 'initialized' $false) { $fallback = Save-ComoLegacyImage }
                } catch {
                    Write-ComoLog '移行前の版は動作確認できなかったため、復旧先には登録しません。'
                }
            }
            if (-not (Test-ComoImage $candidate)) {
                if ($Rollback -or $candidate -ne $checkout) {
                    throw (New-ComoError '保持していたアプリの版が見つかりません。「アプリを更新」で現在の版を準備できます。' 'release')
                }
                Write-ComoStep $Operation 'アプリの準備' 'この版のコモを準備しています。初回や更新時は数分かかることがあります。'
                Invoke-ComoCompose @('build', 'chu-ai') -Revision $candidate | Out-Null
            }
            Write-ComoStep $Operation 'アプリの起動' 'コモのサービスを起動しています。'
            $composeArgs = @('up', '-d', '--no-build')
            if ($Restart) { $composeArgs += '--force-recreate' }
            $switched = $true
            Invoke-ComoCompose $composeArgs -Revision $candidate | Out-Null
            $health = Wait-ComoHealth $candidate
        }
        if ($HealthOnly) { return $health }

        $checkingReadiness = $true
        $canProbe = $candidate -ne 'legacy'
        $verifyCandidate = $CheckChat -or (-not $Rollback -and $candidate -ne $current -and [bool]$fallback)
        if ($verifyCandidate -and $canProbe) {
            Write-ComoStep $Operation '接続の確認' 'Geminiへ接続できるか確認しています。'
            Invoke-ComoApi '/api/admin/knowledge/check-connection' 'POST' 75 | Out-Null
        }
        Write-ComoStep $Operation '知識の確認' 'コモが知識を使える状態か確認しています。'
        Wait-ComoKnowledge $Operation | Out-Null
        if (($verifyCandidate -or $pauseWasOn) -and $canProbe) {
            Write-ComoStep $Operation '回答の確認' '試しの質問にコモが回答できるか確認しています。'
            Invoke-ComoApi '/api/admin/knowledge/check-chat' 'POST' 120 | Out-Null
        }
        Set-ComoKioskPaused $false
        if ($candidate -ne $current -and $fallback) { $previous = $fallback }
        if ($candidate -eq $failed) { $failed = '' }
        Write-ComoJson $script:ComoReleasePath @{
            checkout = $checkout; current = $candidate; previous = $previous
            env_hash = $envHash; failed = $failed
        }
        return $health
    } catch {
        $failure = $_
        if ($failure.Exception.Data['como_code'] -eq 'busy') { throw }
        $recovered = $false
        if ($fallback -and $candidate -ne $fallback -and -not $HealthOnly) {
            try {
                if ($switched) {
                    Write-ComoStep $Operation '前の版へ復旧' '直前に使っていた版を開き直しています。'
                    if (-not (Test-ComoImage $fallback)) { throw '前の版のイメージがありません。' }
                    Invoke-ComoCompose @('up', '-d', '--no-build') -Revision $fallback | Out-Null
                    Wait-ComoHealth $fallback 90 | Out-Null
                } else {
                    if ($null -eq (Get-ComoHealth)) { throw '前の版の稼働状態を確認できません。' }
                }
                $state = Get-ComoKnowledgeState
                if (Get-ComoValue $state 'initialization_required' $true) { throw '前の版の知識の準備が必要です。' }
                Set-ComoKioskPaused $pauseWasOn
                Write-ComoJson $script:ComoReleasePath @{
                    checkout = $checkout; current = $fallback; previous = $previous
                    env_hash = $envHash; failed = $candidate
                }
                $recovered = $true
            } catch {
                Write-ComoLog ('前の版の復旧を確認できませんでした: ' + $_.Exception.Message)
            }
        }
        if ($recovered) {
            $code = [string]$failure.Exception.Data['como_code']
            if (-not $code) { $code = 'release' }
            throw (New-ComoError ('選んだ版を利用できなかったため、前の版に戻しました。' + [Environment]::NewLine + $failure.Exception.Message) $code)
        }
        if ($checkingReadiness -and $null -ne (Get-ComoHealth)) { Set-ComoKioskPaused $true }
        throw $failure
    }
}
