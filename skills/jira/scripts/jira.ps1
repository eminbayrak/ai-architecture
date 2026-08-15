# Jira Data Center CLI for Windows. Uses PowerShell + Invoke-WebRequest only
# (ships with Windows). No MSYS2, Git Bash, Python, or extra packages.
# Do not print JIRA_TOKEN. Do not enable tracing.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-JiraUrl([string]$Path) {
  $base = $env:JIRA_BASE
  if (-not $base) { $base = $env:JIRA_BASE_URL }
  if ($Path -match '^https?://') { return $Path }
  $base = $base.TrimEnd("/")
  if (-not $Path.StartsWith("/")) { $Path = "/$Path" }
  return "$base$Path"
}

function Get-IssueKey([string]$Raw) {
  $m = [regex]::Match($Raw, "[A-Z][A-Z0-9]+-[0-9]+")
  if (-not $m.Success) {
    [Console]::Error.WriteLine("Could not parse issue key from: $Raw")
    exit 1
  }
  return $m.Value
}

function Import-KvFile([string]$File) {
  if (-not (Test-Path -LiteralPath $File)) { return }
  Get-Content -LiteralPath $File | ForEach-Object {
    $line = $_.Trim().TrimStart([char]0xFEFF)
    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
    if ($line -eq "" -or $line.StartsWith("#") -or $line -notmatch "=") { return }
    $eq = $line.IndexOf("=")
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim().Trim("'").Trim('"')
    if ($key -notin @("JIRA_BASE", "JIRA_BASE_URL", "JIRA_TOKEN", "JIRA_TOKEN_FILE", "JIRA_ENV_FILE")) {
      return
    }
    $existing = [Environment]::GetEnvironmentVariable($key)
    if (-not $existing) {
      Set-Item -Path "Env:$key" -Value $val
    }
  }
}

function Import-DotEnvWalk([string]$Start) {
  $cur = $Start
  if (-not $cur) { return }
  if (Test-Path -LiteralPath $cur -PathType Leaf) {
    $cur = Split-Path -Parent $cur
  }
  for ($i = 0; $i -lt 8; $i++) {
    if (-not $cur) { break }
    Import-KvFile (Join-Path $cur ".env")
    $parent = Split-Path -Parent $cur
    if (-not $parent -or $parent -eq $cur) { break }
    $cur = $parent
  }
}

function Get-TokenCandidates {
  if ($env:JIRA_TOKEN_FILE) {
    return @($env:JIRA_TOKEN_FILE)
  }
  $list = New-Object System.Collections.Generic.List[string]
  if ($env:HOME) {
    $list.Add((Join-Path $env:HOME ".config\atlassian\jira.token"))
  }
  if ($env:USERPROFILE) {
    $list.Add((Join-Path $env:USERPROFILE ".config\atlassian\jira.token"))
  }
  return $list
}

function Load-JiraEnv {
  if ($env:JIRA_ENV_FILE) { Import-KvFile $env:JIRA_ENV_FILE }
  Import-DotEnvWalk (Get-Location).Path
  if ($PSScriptRoot) { Import-DotEnvWalk $PSScriptRoot }

  if (-not $env:JIRA_TOKEN) {
    foreach ($tokenFile in Get-TokenCandidates) {
      if (Test-Path -LiteralPath $tokenFile) {
        $env:JIRA_TOKEN = ((Get-Content -LiteralPath $tokenFile -Raw) -replace "\s", "")
        break
      }
    }
  }
  if (-not $env:JIRA_BASE -and $env:JIRA_BASE_URL) {
    $env:JIRA_BASE = $env:JIRA_BASE_URL
  }
  if (-not $env:JIRA_BASE) {
    [Console]::Error.WriteLine("Missing JIRA_BASE (or JIRA_BASE_URL). Set it in the environment or .env.")
    exit 1
  }
  if (-not $env:JIRA_TOKEN) {
    [Console]::Error.WriteLine("Missing Jira token. Put a Data Center PAT in %USERPROFILE%\.config\atlassian\jira.token, or set JIRA_TOKEN / JIRA_ENV_FILE.")
    exit 1
  }
}

function Write-DryRun([string]$Method, [string]$Url, [string]$Body) {
  Write-Output "curl -sS -X $Method \"
  Write-Output "  -H 'Authorization: Bearer ***' \"
  if ($Body) {
    Write-Output "  -H 'Accept: application/json' \"
    Write-Output "  -H 'Content-Type: application/json' \"
    Write-Output "  --data $Body \"
  } else {
    Write-Output "  -H 'Accept: application/json' \"
  }
  Write-Output "  '$Url'"
}

function Invoke-JiraRaw {
  param(
    [string]$Method,
    [string]$Path,
    [string]$Body = "",
    [switch]$Dry
  )
  Load-JiraEnv
  $url = Get-JiraUrl $Path
  if ($Dry -or $env:JIRA_DRY_RUN -eq "1") {
    Write-DryRun $Method $url $Body
    return
  }
  $headers = @{
    Authorization = "Bearer $($env:JIRA_TOKEN)"
    Accept        = "application/json"
  }
  $params = @{
    Uri             = $url
    Method          = $Method
    Headers         = $headers
    UseBasicParsing = $true
  }
  if ($Body) {
    $params.Body = $Body
    $params.ContentType = "application/json"
  }
  try {
    $resp = Invoke-WebRequest @params
  } catch [System.Net.WebException] {
    $ex = $_.Exception
    $http = $ex.Response
    if ($http) {
      $code = [int]$http.StatusCode
      $reader = New-Object System.IO.StreamReader($http.GetResponseStream())
      $errBody = $reader.ReadToEnd()
      [Console]::Error.WriteLine("Jira HTTP $code for $Method $url")
      [Console]::Error.WriteLine($errBody)
    } else {
      [Console]::Error.WriteLine("Jira request failed: $($ex.Message)")
    }
    exit 1
  }
  $code = [int]$resp.StatusCode
  if ($code -lt 200 -or $code -ge 300) {
    [Console]::Error.WriteLine("Jira HTTP $code for $Method $url")
    [Console]::Error.WriteLine($resp.Content)
    exit 1
  }
  Write-Output $resp.Content
}

function Find-TransitionId([string]$Want, [string]$Json) {
  $data = $Json | ConvertFrom-Json
  $wantLower = $Want.Trim().ToLowerInvariant()
  foreach ($t in @($data.transitions)) {
    $names = @($t.name, $(if ($t.to) { $t.to.name } else { $null }))
    foreach ($n in $names) {
      if ($n -and $n.ToLowerInvariant() -eq $wantLower) {
        Write-Output ([string]$t.id)
        return
      }
    }
  }
  [Console]::Error.WriteLine("No transition matching '$Want'")
  exit 1
}

function Show-Usage {
  @"
Usage:
  jira GET|POST|PUT|DELETE <path> [json-body]
  jira get <KEY-or-browse-URL>
  jira transitions <KEY-or-URL>
  jira transition <KEY-or-URL> <status-name>
  jira comment <KEY-or-URL> <text>
  jira create <PROJECT> <summary> [--type Task]
  jira parse-key <KEY-or-URL>
  jira find-transition <status-name>   # JSON on stdin

Prefix any command with --dry-run to print curl (token redacted).
Windows: jira.cmd or powershell -File jira.ps1. No extra installs.
"@ | Write-Output
}

$argv = @($args)
$dry = $false
if ($argv.Count -gt 0 -and $argv[0] -eq "--dry-run") {
  $dry = $true
  if ($argv.Count -gt 1) { $argv = $argv[1..($argv.Count - 1)] } else { $argv = @() }
}

$cmd = ""
if ($argv.Count -gt 0) { $cmd = $argv[0] }

# -CaseSensitive keeps the raw method `GET` from also matching the `get` shortcut.
# `break` is required because a PowerShell switch runs every branch that matches.
switch -Regex -CaseSensitive ($cmd) {
  "^$|^-h$|^--help$|^help$" { Show-Usage; exit 0 }
  "^parse-key$" {
    if ($argv.Count -lt 2) { [Console]::Error.WriteLine("key or URL required"); exit 1 }
    Write-Output (Get-IssueKey $argv[1])
    break
  }
  "^find-transition$" {
    if ($argv.Count -lt 2) { [Console]::Error.WriteLine("status name required"); exit 1 }
    $stdinJson = [Console]::In.ReadToEnd()
    Find-TransitionId $argv[1] $stdinJson
    break
  }
  "^(GET|POST|PUT|DELETE|PATCH)$" {
    $path = ""
    $body = ""
    if ($argv.Count -ge 2) { $path = $argv[1] }
    if ($argv.Count -ge 3) { $body = $argv[2] }
    Invoke-JiraRaw -Method $cmd -Path $path -Body $body -Dry:$dry
    break
  }
  "^get$" {
    if ($argv.Count -lt 2) { [Console]::Error.WriteLine("issue key or URL required"); exit 1 }
    $key = Get-IssueKey $argv[1]
    Invoke-JiraRaw -Method GET -Path "/rest/api/2/issue/$key" -Dry:$dry
    break
  }
  "^transitions$" {
    if ($argv.Count -lt 2) { [Console]::Error.WriteLine("issue key or URL required"); exit 1 }
    $key = Get-IssueKey $argv[1]
    Invoke-JiraRaw -Method GET -Path "/rest/api/2/issue/$key/transitions" -Dry:$dry
    break
  }
  "^transition$" {
    if ($argv.Count -lt 3) { [Console]::Error.WriteLine("issue key and status name required"); exit 1 }
    $key = Get-IssueKey $argv[1]
    $statusName = $argv[2]
    if ($dry) {
      Invoke-JiraRaw -Method GET -Path "/rest/api/2/issue/$key/transitions" -Dry
      Write-Output "# then POST /rest/api/2/issue/$key/transitions with the matching transition id for '$statusName'"
      exit 0
    }
    $transJson = Invoke-JiraRaw -Method GET -Path "/rest/api/2/issue/$key/transitions"
    $tid = Find-TransitionId $statusName $transJson
    $body = "{`"transition`":{`"id`":`"$tid`"}}"
    Invoke-JiraRaw -Method POST -Path "/rest/api/2/issue/$key/transitions" -Body $body | Out-Null
    Write-Output "Transitioned $key -> $statusName (id $tid)"
    break
  }
  "^comment$" {
    if ($argv.Count -lt 3) { [Console]::Error.WriteLine("issue key and comment text required"); exit 1 }
    $key = Get-IssueKey $argv[1]
    $text = $argv[2]
    $body = (@{ body = $text } | ConvertTo-Json -Compress)
    Invoke-JiraRaw -Method POST -Path "/rest/api/2/issue/$key/comment" -Body $body -Dry:$dry
    break
  }
  "^create$" {
    if ($argv.Count -lt 3) { [Console]::Error.WriteLine("project key and summary required"); exit 1 }
    $project = $argv[1]
    $summary = $argv[2]
    $itype = "Task"
    if ($argv.Count -ge 5 -and $argv[3] -eq "--type") { $itype = $argv[4] }
    $payload = @{
      fields = @{
        project   = @{ key = $project }
        summary   = $summary
        issuetype = @{ name = $itype }
      }
    }
    $body = ($payload | ConvertTo-Json -Compress -Depth 6)
    Invoke-JiraRaw -Method POST -Path "/rest/api/2/issue" -Body $body -Dry:$dry
    break
  }
  default {
    [Console]::Error.WriteLine("Unknown command: $cmd")
    Show-Usage
    exit 1
  }
}
