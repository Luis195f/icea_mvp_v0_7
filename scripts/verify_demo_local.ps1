Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OriginalLocation = Get-Location
$BackendDataPath = Join-Path $RepoRoot "backend\data"
$BackendDataPreexisted = Test-Path -LiteralPath $BackendDataPath
$PythonExe = $null
$PythonArgsPrefix = @()
$NpmExe = $null
$NodeExe = $null

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    Write-Host ""
    Write-Host "== $Name"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    try {
        & $FilePath @Arguments "--version" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-DemoPython {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvPython) -and (Test-PythonCommand -FilePath $venvPython)) {
        $script:PythonExe = $venvPython
        $script:PythonArgsPrefix = @()
        Write-Host "Using local venv Python."
        return
    }

    if (Get-Command "python" -ErrorAction SilentlyContinue) {
        if (Test-PythonCommand -FilePath "python") {
            $script:PythonExe = "python"
            $script:PythonArgsPrefix = @()
            Write-Host "Using PATH Python because local venv Python is unavailable."
            return
        }
    }

    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        if (Test-PythonCommand -FilePath "py" -Arguments @("-3")) {
            $script:PythonExe = "py"
            $script:PythonArgsPrefix = @("-3")
            Write-Host "Using py -3 because local venv Python is unavailable."
            return
        }
    }

    throw "No usable Python runtime found. Repair .venv or make python/py available before running the demo verifier."
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @()
    )

    $allArguments = @($PythonArgsPrefix) + @($Arguments)
    Invoke-Native -Name $Name -FilePath $PythonExe -Arguments $allArguments
}

function Resolve-DemoNode {
    if (Get-Command "npm" -ErrorAction SilentlyContinue) {
        $script:NpmExe = "npm"
        Write-Host "Using npm from PATH for frontend scripts."
        return
    }

    if ($env:NODE_EXE -and (Test-Path -LiteralPath $env:NODE_EXE)) {
        $script:NodeExe = $env:NODE_EXE
        Write-Host "Using NODE_EXE fallback for frontend scripts because npm is unavailable."
        return
    }

    if (Get-Command "node" -ErrorAction SilentlyContinue) {
        $script:NodeExe = "node"
        Write-Host "Using node fallback for frontend scripts because npm is unavailable."
        return
    }

    throw "No usable npm or node runtime found. Install/restore npm, put node on PATH, or set NODE_EXE to node.exe before running the demo verifier."
}

function Invoke-FrontendScript {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("test", "lint", "build")][string]$ScriptName
    )

    if ($NpmExe) {
        if ($ScriptName -eq "build") {
            Invoke-Native -Name "Frontend build" -FilePath $NpmExe -Arguments @("run", "build")
        }
        else {
            Invoke-Native -Name "Frontend $ScriptName" -FilePath $NpmExe -Arguments @("run", $ScriptName, "--if-present")
        }
        return
    }

    if ($ScriptName -eq "test") {
        Invoke-Native -Name "Frontend test fallback" -FilePath $NodeExe -Arguments @("scripts\run-typescript-contract.cjs", "tests\icea-compute-contract.ts")
    }
    elseif ($ScriptName -eq "lint") {
        Invoke-Native -Name "Frontend lint fallback" -FilePath $NodeExe -Arguments @("node_modules\next\dist\bin\next", "lint")
    }
    else {
        Invoke-Native -Name "Frontend build fallback" -FilePath $NodeExe -Arguments @("node_modules\next\dist\bin\next", "build")
    }
}

function Set-DemoSecrets {
    Write-Host ""
    Write-Host "== Generating ephemeral local demo secrets"
    $script = @"
import base64
import json
import os
import secrets

print(json.dumps({
    "SECRET_KEY": secrets.token_urlsafe(48),
    "JWT_SIGNING_KEY": secrets.token_urlsafe(48),
    "AUDIT_LOG_SECRET": secrets.token_urlsafe(48),
    "PHI_ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
}))
"@
    $allArguments = @($PythonArgsPrefix) + @("-c", $script)
    $json = & $PythonExe @allArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Ephemeral secret generation failed with exit code $LASTEXITCODE"
    }
    $generated = $json | ConvertFrom-Json

    $env:DJANGO_DEBUG = "false"
    $env:SECRET_KEY = $generated.SECRET_KEY
    $env:ALLOWED_HOSTS = "localhost,127.0.0.1,testserver"
    $env:ICEA_SECURE_MODE = "true"
    $env:ICEA_DEV_ALLOW_INSECURE = "false"
    $env:ICEA_AUTH_REQUIRED = "true"
    $env:ICEA_RBAC_ENFORCE = "true"
    $env:JWT_SIGNING_KEY = $generated.JWT_SIGNING_KEY
    $env:AUDIT_LOG_SECRET = $generated.AUDIT_LOG_SECRET
    $env:PHI_ENCRYPTION_KEYS = $generated.PHI_ENCRYPTION_KEYS
    $env:ICEA_ENABLE_THROTTLING = "true"
    $env:CORS_ALLOW_ALL_ORIGINS = "false"

    Write-Host "Ephemeral values are set for this process only; values were not printed."
}

function Set-DemoStrictMode {
    $env:DJANGO_DEBUG = "false"
    $env:ALLOWED_HOSTS = "localhost,127.0.0.1,testserver"
    $env:ICEA_SECURE_MODE = "true"
    $env:ICEA_DEV_ALLOW_INSECURE = "false"
    $env:ICEA_AUTH_REQUIRED = "true"
    $env:ICEA_RBAC_ENFORCE = "true"
    $env:ICEA_ENABLE_THROTTLING = "true"
    $env:CORS_ALLOW_ALL_ORIGINS = "false"
}

function Set-BackendTestMode {
    $env:DJANGO_DEBUG = "false"
    $env:ICEA_SECURE_MODE = "false"
    Remove-Item Env:\ICEA_DEV_ALLOW_INSECURE -ErrorAction SilentlyContinue
    Remove-Item Env:\ICEA_AUTH_REQUIRED -ErrorAction SilentlyContinue
    Remove-Item Env:\ICEA_RBAC_ENFORCE -ErrorAction SilentlyContinue
    Remove-Item Env:\SECURE_SSL_REDIRECT -ErrorAction SilentlyContinue
    Write-Host "Backend unit tests will run in the repo's normal test posture; strict demo mode is restored for readiness/smoke."
}

try {
    Set-Location -LiteralPath $RepoRoot

    Invoke-Native "Initial git status" "git" @("status", "--short", "--branch")
    Invoke-Native "Whitespace/conflict-marker check" "git" @("diff", "--check")
    Resolve-DemoPython
    Resolve-DemoNode
    Set-DemoSecrets

    Push-Location -LiteralPath (Join-Path $RepoRoot "backend")
    try {
        Invoke-Python "Backend migrations" @("manage.py", "migrate")
        Invoke-Python "Seed synthetic demo data" @("manage.py", "seed_demo", "--rows", "800", "--name", "icea-demo", "--model-version", "v1")
        Set-BackendTestMode
        Invoke-Python "Backend test suite" @("manage.py", "test", "-v", "2")
        Set-DemoStrictMode
        Invoke-Python "ICEA readiness strict check" @("manage.py", "icea_readiness_check", "--strict-exit")
        Invoke-Python "ICEA smoke strict check" @("manage.py", "icea_smoke_test", "--strict-exit")
    }
    finally {
        Pop-Location
    }

    Push-Location -LiteralPath (Join-Path $RepoRoot "frontend\icea-nursing-command-center")
    try {
        Invoke-FrontendScript "test"
        Invoke-FrontendScript "lint"
        Invoke-FrontendScript "build"
    }
    finally {
        Pop-Location
    }
}
finally {
    Set-Location -LiteralPath $OriginalLocation

    Write-Host ""
    Write-Host "== Cleaning generated backend demo data"
    if ((Test-Path -LiteralPath $BackendDataPath) -and (-not $BackendDataPreexisted)) {
        Remove-Item -LiteralPath $BackendDataPath -Recurse -Force
        Write-Host "Removed backend\data created during this run."
    }
    elseif (Test-Path -LiteralPath (Join-Path $BackendDataPath "demo_dataset.json")) {
        Remove-Item -LiteralPath (Join-Path $BackendDataPath "demo_dataset.json") -Force
        Write-Host "Removed backend\data\demo_dataset.json; preserved pre-existing backend\data."
    }
    else {
        Write-Host "No generated backend demo data found."
    }

    Set-Location -LiteralPath $RepoRoot
    Invoke-Native "Final git status" "git" @("status", "--short", "--branch")
    Set-Location -LiteralPath $OriginalLocation
}

Write-Host ""
Write-Host "Local free demo verification completed without paid services, cloud calls, commits, pushes, branch changes, or printed secrets."
