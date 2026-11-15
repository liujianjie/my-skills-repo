<#
.SYNOPSIS
    在 DSH 技能目录（%USERPROFILE%\.dsh\skills）与本地仓库（本仓库 skills\）之间双向同步自建 skill。

.DESCRIPTION
    -Direction Pull  ：DSH → 仓库（在 .dsh 里改完脚本后收进版本库）
    -Direction Push  ：仓库 → DSH（新机器恢复，或把仓库改动装回 DSH 生效）
    -Name 省略时，处理仓库 skills\ 下已有的全部 skill 目录。
    -Prune 打开镜像删除（robocopy /MIR）；默认只增量覆盖，不删目标端多余文件。

.EXAMPLE
    pwsh -File .\scripts\sync-from-dsh.ps1 -Direction Pull
    pwsh -File .\scripts\sync-from-dsh.ps1 -Direction Pull -Name xhs-profile-notes -DryRun
    pwsh -File .\scripts\sync-from-dsh.ps1 -Direction Push -Name suno-download
#>
[CmdletBinding()]
param(
    [ValidateSet('Pull', 'Push')]
    [string]$Direction = 'Pull',

    [string[]]$Name,

    [string]$DshSkills = (Join-Path $HOME '.dsh\skills'),

    [string]$RepoSkills,

    [switch]$Prune,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if (-not $RepoSkills) {
    $RepoSkills = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\skills'))
}

if (-not (Test-Path -LiteralPath $RepoSkills)) {
    throw "仓库 skills 目录不存在: $RepoSkills"
}
if (-not (Test-Path -LiteralPath $DshSkills)) {
    throw "DSH 技能目录不存在: $DshSkills（用 -DshSkills 指定）"
}

# 省略 -Name 时，按仓库现有目录推断
if (-not $Name -or $Name.Count -eq 0) {
    $Name = Get-ChildItem -LiteralPath $RepoSkills -Directory | Select-Object -ExpandProperty Name
}

if (-not $Name -or $Name.Count -eq 0) {
    throw "没有要同步的 skill：仓库 $RepoSkills 下为空，且未指定 -Name。"
}

$roboArgs = @('/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
if ($Prune) { $roboArgs += '/MIR' } else { $roboArgs += '/XO' }
if ($DryRun) { $roboArgs += '/L' }

# 产物与缓存不同步
$roboArgs += @('/XD', '__pycache__', '.venv', '.state', 'outputs', 'out', 'downloads', '.pytest_cache')
$roboArgs += @('/XF', '*.pyc', '*.mp3', '*.m4a', '*.wav', 'login_state.json', 'notes_all.json', 'notes_digest.md')

Write-Host ("方向 {0} | 仓库 {1} | DSH {2} | 技能 {3}" -f $Direction, $RepoSkills, $DshSkills, ($Name -join ', '))

$failed = @()
foreach ($n in $Name) {
    $repoPath = Join-Path $RepoSkills $n
    $dshPath = Join-Path $DshSkills $n

    if ($Direction -eq 'Pull') {
        if (-not (Test-Path -LiteralPath $dshPath)) {
            Write-Warning "跳过 $n：DSH 里没有 $dshPath"
            $failed += $n
            continue
        }
        $src, $dst = $dshPath, $repoPath
    }
    else {
        if (-not (Test-Path -LiteralPath $repoPath)) {
            Write-Warning "跳过 $n：仓库里没有 $repoPath"
            $failed += $n
            continue
        }
        $src, $dst = $repoPath, $dshPath
    }

    if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $dst | Out-Null }

    Write-Host ("`n[{0}] {1} -> {2}" -f $n, $src, $dst) -ForegroundColor Cyan
    robocopy $src $dst @roboArgs | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Warning "robocopy 失败（exit $code）：$n"
        $failed += $n
    }
    else {
        $note = if ($code -eq 0) { '无变化' } else { "已同步 (exit $code)" }
        Write-Host "  $note" -ForegroundColor Green
    }
}

Write-Host ''
if ($failed.Count -gt 0) {
    Write-Host ("完成，但有 {0} 项未同步: {1}" -f $failed.Count, ($failed -join ', ')) -ForegroundColor Yellow
    exit 1
}
Write-Host '全部完成。' -ForegroundColor Green
if ($Direction -eq 'Pull') {
    Write-Host '提示：git status 复查后提交；确认没有 .state/ 与产物文件混入。' -ForegroundColor DarkGray
}
