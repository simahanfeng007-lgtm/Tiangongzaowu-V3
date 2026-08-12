[CmdletBinding()]
param(
    [string]$Gateway = "http://127.0.0.1:7184",
    [string]$LifeName = "p15_experiment",
    [string]$ReportPath = "",
    [switch]$SkipIdentity
)

# P15 对话实验辅助：对 30 组场景中可 API 化的部分自动落库/召回/纠错/删除验证。
# 对话措辞部分仍需在聊天窗口逐条实测后回填 docs/p15-chat-experiments-30.md。

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ReportPath) {
    $ReportPath = Join-Path $Root "docs\p15-chat-experiments-report.jsonl"
}
$Report = [System.Collections.Generic.List[object]]::new()

function Invoke-LifeApi {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $params = @{
        Uri         = "$Gateway$Path"
        Method      = $Method
        ContentType = "application/json; charset=utf-8"
        TimeoutSec  = 120
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 8)
    }
    try {
        $response = Invoke-RestMethod @params
        return @{ ok = $true; value = $response }
    } catch {
        return @{
            ok     = $false
            status = $_.Exception.Response.StatusCode.value__
            error  = [string]$_.ErrorDetails.Message
        }
    }
}

function Add-Report {
    param([string]$Scenario, [string]$Action, [bool]$Ok, [object]$Detail)
    $Report.Add([pscustomobject]@{
        scenario = $Scenario
        action   = $Action
        ok       = $Ok
        detail   = $Detail
        ts       = (Get-Date -Format "o")
    })
}

function Check-Db {
    param([string]$Sql)
    $profileRoot = Join-Path $env:LOCALAPPDATA "TiangongV3-SourceWork"
    $db = Join-Path $profileRoot "runtime\life-authority.shadow.sqlite3"
    if (-not (Test-Path -LiteralPath $db)) {
        return "db_missing"
    }
    $py = Join-Path $Root "app\runtime\python312\python.exe"
    $code = "import sqlite3;c=sqlite3.connect(r'$db');print(c.execute($Sql).fetchall())"
    try {
        return (& $py -c $code 2>$null | Out-String).Trim()
    } catch {
        return "db_error"
    }
}

Write-Host "Gateway: $Gateway"
$health = Invoke-LifeApi "GET" "/api/v1/v3/life/health" $null
if (-not $health.ok) {
    throw "Gateway 不可达（$($health.error)）。请先启动源码版并确保 7184 在监听。"
}
Write-Host "Gateway 可达。注意：若 7184 是重启前启动的旧代码，L4 校验会失败，请重启后再跑。"

# 测试身份
$LifeId = ""
if (-not $SkipIdentity) {
    $created = Invoke-LifeApi "POST" "/api/v1/v3/life/identity/create" @{ name = $LifeName }
    if ($created.ok) {
        $LifeId = [string]$created.value.identity.life_id
    } else {
        $active = Invoke-LifeApi "GET" "/api/v1/v3/life/identity/active" $null
        if ($active.ok) { $LifeId = [string]$active.value.life_id }
    }
}
if (-not $LifeId) {
    throw "无法解析测试 life_id。"
}
Write-Host "测试 life_id: $LifeId"

function Assert-Memory {
    param([string]$Scenario, [string]$Text)
    $r = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/assert" @{
        life_id          = $LifeId
        content          = @{ text = $Text }
        epistemic_status = "user_asserted"
        actor            = "user"
    }
    $ok = $r.ok -and $r.value.ok
    Add-Report $Scenario "assert:$Text" $ok ($r.value | ConvertTo-Json -Depth 6 -Compress)
    return $r
}

function Search-Memory {
    param([string]$Scenario, [string]$Query)
    $r = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/search" @{
        life_id = $LifeId
        query   = $Query
        limit   = 12
    }
    $hits = @()
    if ($r.ok -and $r.value.results) {
        foreach ($row in $r.value.results) {
            $content = $row.content
            if ($content -is [string]) { $hits += $content }
            elseif ($content -is [System.Management.Automation.PSCustomObject]) { $hits += [string]$content.text }
        }
    }
    Add-Report $Scenario "search:$Query" ($hits.Count -gt 0) (($hits | Select-Object -First 5) -join " | ")
    return $hits
}

# A1-A7 显式记忆落库
Assert-Memory "A1" "记住，我叫老于。"
Assert-Memory "A2" "我的名字是老于，记住。"
Assert-Memory "A3" "今天先叫我小A。"
Assert-Memory "A4" "记住，地球是平的。"
Assert-Memory "A5" "请长期保存：每天备份一次。"
Assert-Memory "A6" "不要忘记我的邮箱是 test@example.com。"
Assert-Memory "A7" "我的长期偏好是回复简洁。"

# A8 普通对话不落库（turn 不产生 L4）
$turn = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/turn" @{
    life_id        = $LifeId
    conversation_id = "p15_exp"
    turn_id        = "p15_exp_plain_1"
    user_text      = "今天天气不错。"
    assistant_text = "嗯，今天天气确实不错。"
}
Add-Report "A8" "turn:普通对话" $turn.ok ($turn.value | ConvertTo-Json -Depth 5 -Compress)

# C 召回（15-20）
Search-Memory "C15" "我叫什么"
Search-Memory "C16" "我的名字"
Search-Memory "C17" "我之前说过什么"
Search-Memory "C20" "我的偏好"

# B9 纠错
$r = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/correct" @{
    life_id          = $LifeId
    target_memory_id = ""
    content          = @{ text = "纠正：我的名字叫小红。" }
    actor            = "user"
}
Add-Report "B9" "correct:名字" ($r.ok -and $r.value.ok) ($r.value | ConvertTo-Json -Depth 6 -Compress)
Search-Memory "B9" "小红"

# B10 删除（先 assert 电话再删除）
$phone = Assert-Memory "B10" "记住我的电话是 13800000000。"
if ($phone.ok) {
    $memoryId = [string]$phone.value.contract_memory_id
    $del = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/delete" @{
        life_id   = $LifeId
        memory_id = $memoryId
        actor     = "user"
    }
    Add-Report "B10" "delete:$memoryId" ($del.ok -and $del.value.ok) ($del.value | ConvertTo-Json -Depth 6 -Compress)
}
Search-Memory "B10" "13800000000"

# B13 重复删除（幂等）
if ($phone.ok) {
    $memoryId = [string]$phone.value.contract_memory_id
    $del2 = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/delete" @{
        life_id   = $LifeId
        memory_id = $memoryId
        actor     = "user"
    }
    Add-Report "B13" "delete-again:$memoryId" ($del2.ok) ($del2.value | ConvertTo-Json -Depth 6 -Compress)
}

# D22 secret 不投影（DB 检查 world outbox 为空即可作为对照；secret 由契约拒绝）
$secret = Invoke-LifeApi "POST" "/api/v1/v3/life/memory/assert" @{
    life_id          = $LifeId
    content          = @{ text = "记住一个秘密口令 hunter2。" }
    epistemic_status = "user_asserted"
    actor            = "user"
}
Add-Report "D22" "assert:secret" $secret.ok ($secret.value | ConvertTo-Json -Depth 6 -Compress)

# E25 普通轮不改性格（对比前后）
$t1 = Invoke-LifeApi "GET" "/api/v1/v3/life/temperament" $null
for ($i = 1; $i -le 10; $i++) {
    Invoke-LifeApi "POST" "/api/v1/v3/life/memory/turn" @{
        life_id         = $LifeId
        conversation_id = "p15_exp"
        turn_id         = "p15_exp_turn_$i"
        user_text       = "第 $i 轮普通对话内容。"
        assistant_text  = "收到。"
    } | Out-Null
}
$t2 = Invoke-LifeApi "GET" "/api/v1/v3/life/temperament" $null
$changed = ($t1.ok -and $t2.ok -and $t1.value.temperament -ne $t2.value.temperament)
Add-Report "E25" "temperament:10轮" (-not $changed) "before=$($t1.value.temperament.revision) after=$($t2.value.temperament.revision)"

# L4 落库检查（A1-A7 应有 L4）
$l4 = Check-Db "SELECT count(*) FROM memory_derivations WHERE life_id='$LifeId' AND layer='L4_EXPLICIT'"
Add-Report "DB" "l4_count" ($l4 -match "\((\d+),?\)" -and $Matches[1] -ge 1) $l4

# 写出报告
$Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
$passed = @($Report | Where-Object { $_.ok }).Count
$failed = $Report.Count - $passed
Write-Host "完成：共 $($Report.Count) 条记录，通过 $passed，失败/待人工 $failed。"
Write-Host "报告：$ReportPath"
