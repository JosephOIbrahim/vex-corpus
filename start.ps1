<#
.SYNOPSIS
    VEX Corpus Generator - One-Command Autonomous Launcher

.DESCRIPTION
    Starts Ollama (if needed), pre-loads Nemotron models, and launches
    the autonomous VEX corpus processing daemon.

.PARAMETER Mode
    run     - Start autonomous daemon (default)
    gui     - Start PyQt6 GUI
    once    - Process a folder once and exit
    test    - Run integration tests
    status  - Check system status

.PARAMETER WatchDir
    Directory to watch for VEX files (default: ./input)

.PARAMETER NoTier2
    Skip Tier 2 generation (faster)

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Mode gui
    .\start.ps1 -Mode once -WatchDir "C:\my\vex\files"
    .\start.ps1 -NoTier2
#>

param(
    [ValidateSet("run", "gui", "once", "test", "status")]
    [string]$Mode = "run",

    [string]$WatchDir = "",

    [switch]$NoTier2,

    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$VexCorpusDir = $PSScriptRoot

function Write-Status($msg) {
    if (-not $Quiet) {
        Write-Host "[VEX] $msg" -ForegroundColor Cyan
    }
}

function Write-Success($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Err($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

# Check Python
function Test-Python {
    try {
        $version = python --version 2>&1
        Write-Status "Python: $version"
        return $true
    } catch {
        Write-Err "Python not found"
        return $false
    }
}

# Check/Start Ollama
function Start-Ollama {
    Write-Status "Checking Ollama..."

    # Check if Ollama is responding
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2
        Write-Success "Ollama is running"
        return $true
    } catch {
        Write-Status "Ollama not responding, attempting to start..."
    }

    # Try to start Ollama
    $ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaPath) {
        # Check common install locations
        $paths = @(
            "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
            "$env:ProgramFiles\Ollama\ollama.exe",
            "C:\Ollama\ollama.exe"
        )
        foreach ($p in $paths) {
            if (Test-Path $p) {
                $ollamaPath = $p
                break
            }
        }
    }

    if (-not $ollamaPath) {
        Write-Err "Ollama not found. Please install from https://ollama.ai"
        return $false
    }

    Write-Status "Starting Ollama service..."
    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden

    # Wait for Ollama to be ready
    $retries = 30
    while ($retries -gt 0) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 | Out-Null
            Write-Success "Ollama started successfully"
            return $true
        } catch {
            $retries--
        }
    }

    Write-Err "Ollama failed to start"
    return $false
}

# Check/Pull Nemotron models
function Ensure-NemotronModels {
    Write-Status "Checking Nemotron models..."

    $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags"
    $models = $response.models | ForEach-Object { $_.name }

    $required = @("nemotron-mini:latest", "nemotron-3-nano:latest")
    $missing = @()

    foreach ($model in $required) {
        $found = $models | Where-Object { $_ -like "*nemotron*" -and $_ -like "*$($model.Split(':')[0].Split('-')[-1])*" }
        if (-not $found) {
            $missing += $model
        }
    }

    if ($missing.Count -gt 0) {
        Write-Status "Pulling missing models: $($missing -join ', ')"
        foreach ($model in $missing) {
            Write-Status "Pulling $model (this may take a while)..."
            & ollama pull $model
            if ($LASTEXITCODE -ne 0) {
                Write-Err "Failed to pull $model"
                return $false
            }
        }
    }

    Write-Success "Nemotron models ready"
    return $true
}

# Pre-warm model
function Warm-Model($model) {
    Write-Status "Pre-warming $model..."

    $body = @{
        model = $model
        prompt = "test"
        options = @{ num_predict = 1 }
    } | ConvertTo-Json

    try {
        Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 60 | Out-Null
        Write-Success "$model loaded into VRAM"
    } catch {
        Write-Status "Warning: Could not pre-warm $model"
    }
}

# Main execution
function Main {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "  VEX CORPUS GENERATOR" -ForegroundColor Yellow
    Write-Host "  Autonomous Nemotron Pipeline" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""

    # Preflight checks
    if (-not (Test-Python)) { exit 1 }
    if (-not (Start-Ollama)) { exit 1 }
    if (-not (Ensure-NemotronModels)) { exit 1 }

    # Pre-warm primary model
    Warm-Model "nemotron-mini:latest"

    Write-Host ""
    Write-Status "Starting in mode: $Mode"
    Write-Host ""

    # Change to project directory
    Set-Location $VexCorpusDir

    # Build command based on mode
    switch ($Mode) {
        "run" {
            $args = @()
            if ($WatchDir) { $args += "--watch"; $args += $WatchDir }
            if ($NoTier2) { $args += "--no-tier2" }

            Write-Success "Launching autonomous daemon..."
            Write-Host ""
            python autonomous.py @args
        }

        "gui" {
            Write-Success "Launching GUI..."
            python run_gui.py
        }

        "once" {
            if (-not $WatchDir) {
                Write-Err "Please specify -WatchDir for once mode"
                exit 1
            }

            $args = @("--once", $WatchDir)
            if ($NoTier2) { $args += "--no-tier2" }

            Write-Success "Processing $WatchDir..."
            python autonomous.py @args
        }

        "test" {
            Write-Success "Running integration tests..."
            python test_integration.py
        }

        "status" {
            Write-Host ""
            Write-Host "System Status" -ForegroundColor Yellow
            Write-Host "=============" -ForegroundColor Yellow

            # Ollama status
            try {
                $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags"
                Write-Host "Ollama: " -NoNewline; Write-Host "Running" -ForegroundColor Green
                Write-Host "Models: $($response.models.Count)"

                $nemotron = $response.models | Where-Object { $_.name -like "*nemotron*" }
                foreach ($m in $nemotron) {
                    $size = [math]::Round($m.size / 1GB, 1)
                    Write-Host "  - $($m.name) (${size}GB)"
                }
            } catch {
                Write-Host "Ollama: " -NoNewline; Write-Host "Not Running" -ForegroundColor Red
            }

            # Check output directory
            $outputDir = Join-Path $VexCorpusDir "output"
            if (Test-Path $outputDir) {
                $files = Get-ChildItem $outputDir -Filter "*.json" | Measure-Object
                Write-Host "Output files: $($files.Count)"
            }
        }
    }
}

Main
