# Rocket League Head-to-Head Overlay - one-click installer.
# Idempotent: safe to re-run. See ../README.md "Quick install" for context.

$ErrorActionPreference = 'Stop'

$REPO_URL = "https://github.com/Florentde29/rl-h2h.git"
$DEFAULT_INSTALL_DIR = Join-Path $env:USERPROFILE "Documents\rl-h2h"
$REQUIRED_PYTHON_MAJOR = 3
$REQUIRED_PYTHON_MINOR = 10
$RL_INI_RELATIVE = "TAGame\Config\DefaultStatsAPI.ini"
$REQUIRED_INI = @{ "PacketSendRate" = "2"; "Port" = "49123" }

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    [ERR] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Has-Command($name) {
    $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Confirm-Yes($prompt) {
    # Default-yes prompt: empty input or anything starting with y/Y means yes.
    $resp = Read-Host "$prompt [Y/n]"
    return ($resp -eq "" -or $resp -match '^[yY]')
}

function Install-WingetPackage($wingetId, $verifyCmd, $displayName) {
    if (-not (Has-Command winget)) {
        throw "winget not found. Install 'App Installer' from the Microsoft Store, or install $displayName manually and re-run."
    }
    Write-Info "Installing $displayName via winget (silent)..."
    & winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "winget install of $displayName failed (exit $LASTEXITCODE)" }
    Refresh-Path
    if (-not (Has-Command $verifyCmd)) {
        throw "$displayName installed but '$verifyCmd' isn't on PATH yet. Restart your computer and re-run this installer."
    }
}

# ── Banner ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Rocket League Head-to-Head Overlay - Installer" -ForegroundColor White
Write-Host "  ----------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Installs Python (if needed), clones the app to" -ForegroundColor DarkGray
Write-Host "  $DEFAULT_INSTALL_DIR," -ForegroundColor DarkGray
Write-Host "  patches Rocket League's Stats API config, and creates shortcuts." -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C at any time to abort." -ForegroundColor DarkGray
Write-Host ""

# ── 1. Python ───────────────────────────────────────────────────────────────
Write-Step "Checking Python"
$needsPython = $true
if (Has-Command python) {
    try {
        $verRaw = (& python --version 2>&1).ToString()
        if ($verRaw -match 'Python (\d+)\.(\d+)') {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            $okVersion = ($maj -gt $REQUIRED_PYTHON_MAJOR) -or
                         ($maj -eq $REQUIRED_PYTHON_MAJOR -and $min -ge $REQUIRED_PYTHON_MINOR)
            if ($okVersion) {
                Write-Ok "Python $maj.$min already installed"
                $needsPython = $false
            } else {
                Write-Warn "Python $maj.$min is too old; need $REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR+"
            }
        }
    } catch {
        Write-Warn "Found python on PATH but couldn't read its version"
    }
}
if ($needsPython) {
    Install-WingetPackage "Python.Python.3.12" "python" "Python 3.12"
    Write-Ok "Python installed"
}

# ── 2. Git ──────────────────────────────────────────────────────────────────
Write-Step "Checking Git"
if (Has-Command git) {
    Write-Ok "Git already installed"
} else {
    Install-WingetPackage "Git.Git" "git" "Git"
    Write-Ok "Git installed"
}

# ── 3. Clone or update repo ─────────────────────────────────────────────────
Write-Step "Setting up the app folder"
$installDir = $DEFAULT_INSTALL_DIR
if (Test-Path $installDir) {
    if (Test-Path (Join-Path $installDir ".git")) {
        Write-Info "Existing install detected at $installDir; pulling latest..."
        Push-Location $installDir
        try {
            & git pull --ff-only
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "git pull failed (exit $LASTEXITCODE) - keeping existing version. Local edits?"
            } else {
                Write-Ok "Updated to latest"
            }
        } finally { Pop-Location }
    } else {
        Write-Warn "$installDir exists but isn't a git clone."
        $resp = Read-Host "Overwrite? Type YES to delete and re-clone (anything else aborts)"
        if ($resp -ne "YES") { throw "Aborted by user." }
        Remove-Item -Recurse -Force $installDir
        & git clone $REPO_URL $installDir
        if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }
        Write-Ok "Cloned fresh"
    }
} else {
    Write-Info "Cloning into $installDir..."
    & git clone $REPO_URL $installDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }
    Write-Ok "Cloned"
}

# ── 4. pip install ──────────────────────────────────────────────────────────
Write-Step "Installing Python dependencies"
$reqFile = Join-Path $installDir "requirements.txt"
try {
    & python -m pip install --upgrade pip --quiet
    & python -m pip install -r $reqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install exit $LASTEXITCODE" }
    Write-Ok "Dependencies installed"
} catch {
    Write-Err "pip install failed: $_"
    Write-Info "If curl_cffi failed to build, common fixes:"
    Write-Info "  - Make sure Python is 3.12 (some wheels lag on newer versions)"
    Write-Info "  - Or install Microsoft C++ Build Tools from https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    throw
}

# ── 5. Detect Rocket League install ─────────────────────────────────────────
Write-Step "Looking for Rocket League"

function Find-RLViaSteam {
    $steamKey = 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam'
    if (-not (Test-Path $steamKey)) { return $null }
    $steamPath = (Get-ItemProperty $steamKey -ErrorAction SilentlyContinue).InstallPath
    if (-not $steamPath -or -not (Test-Path $steamPath)) { return $null }
    $libs = @($steamPath)
    $vdf = Join-Path $steamPath "steamapps\libraryfolders.vdf"
    if (Test-Path $vdf) {
        $content = Get-Content $vdf -Raw
        # Steam stores backslashes as \\ inside the VDF; normalise.
        foreach ($m in [regex]::Matches($content, '"path"\s+"([^"]+)"')) {
            $p = $m.Groups[1].Value -replace '\\\\', '\'
            if ($p -and (Test-Path $p)) { $libs += $p }
        }
    }
    foreach ($lib in $libs | Select-Object -Unique) {
        $candidate = Join-Path $lib "steamapps\common\rocketleague"
        if (Test-Path (Join-Path $candidate "TAGame\Config")) { return $candidate }
    }
    return $null
}

function Find-RLViaEpic {
    $manifestDirs = @()
    $epicKey = 'HKLM:\SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher'
    if (Test-Path $epicKey) {
        $appData = (Get-ItemProperty $epicKey -ErrorAction SilentlyContinue).AppDataPath
        if ($appData) { $manifestDirs += (Join-Path $appData "Manifests") }
    }
    # Well-known default — covers cases where the registry key is absent.
    $manifestDirs += "$env:ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
    foreach ($mdir in $manifestDirs | Select-Object -Unique) {
        if (-not (Test-Path $mdir)) { continue }
        foreach ($f in (Get-ChildItem $mdir -Filter *.item -ErrorAction SilentlyContinue)) {
            try {
                $j = Get-Content $f.FullName -Raw | ConvertFrom-Json
                # "Sugar" is Epic's internal codename for Rocket League. DisplayName is
                # the user-facing fallback in case Epic changes the codename.
                $matchesRL = ($j.DisplayName -eq "Rocket League") -or ($j.MainGameAppName -eq "Sugar")
                if ($matchesRL -and $j.InstallLocation -and
                    (Test-Path (Join-Path $j.InstallLocation "TAGame\Config"))) {
                    return $j.InstallLocation
                }
            } catch { }
        }
    }
    return $null
}

function Pick-RLFolder {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "Find your Rocket League install folder (the one that contains TAGame\Config)"
    $dlg.ShowNewFolderButton = $false
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dlg.SelectedPath
    }
    return $null
}

$rlRoot = Find-RLViaSteam
if ($rlRoot) { Write-Ok "Found Steam install: $rlRoot" }
if (-not $rlRoot) {
    $rlRoot = Find-RLViaEpic
    if ($rlRoot) { Write-Ok "Found Epic install: $rlRoot" }
}
if (-not $rlRoot) {
    Write-Warn "Couldn't auto-detect Rocket League. A folder picker will open."
    $picked = Pick-RLFolder
    if ($picked) {
        if (Test-Path (Join-Path $picked "TAGame\Config")) {
            $rlRoot = $picked
            Write-Ok "Using $rlRoot"
        } else {
            Write-Warn "That folder doesn't contain TAGame\Config; skipping the .ini patch."
            Write-Info "Run this installer again later, or edit DefaultStatsAPI.ini manually (see README)."
        }
    } else {
        Write-Warn "Skipped. Edit DefaultStatsAPI.ini manually (see README) or re-run this installer later."
    }
}

# ── 6. Patch DefaultStatsAPI.ini ────────────────────────────────────────────
if ($rlRoot) {
    Write-Step "Configuring Rocket League's Stats API"
    $iniPath = Join-Path $rlRoot $RL_INI_RELATIVE
    $iniDir = Split-Path $iniPath -Parent
    if (-not (Test-Path $iniDir)) { New-Item -ItemType Directory -Path $iniDir -Force | Out-Null }

    $existing = if (Test-Path $iniPath) { Get-Content $iniPath } else { @() }
    $remaining = $REQUIRED_INI.Clone()
    $alreadyOk = $true
    $newLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $existing) {
        $matched = $false
        foreach ($key in @($remaining.Keys)) {
            if ($line -match "^\s*$key\s*=") {
                $desired = "$key=$($remaining[$key])"
                $newLines.Add($desired)
                if ($line.Trim() -ne $desired) { $alreadyOk = $false }
                $remaining.Remove($key)
                $matched = $true
                break
            }
        }
        if (-not $matched) { $newLines.Add($line) }
    }
    if ($remaining.Count -gt 0) {
        $alreadyOk = $false
        foreach ($key in $remaining.Keys) {
            $newLines.Add("$key=$($remaining[$key])")
        }
    }

    if ($alreadyOk -and (Test-Path $iniPath)) {
        Write-Ok "Already configured: $iniPath"
    } else {
        if ((Test-Path $iniPath) -and -not (Test-Path "$iniPath.bak")) {
            Copy-Item $iniPath "$iniPath.bak"
            Write-Info "Backed up existing file to DefaultStatsAPI.ini.bak"
        }
        Set-Content -Path $iniPath -Value $newLines
        Write-Ok "Patched $iniPath"
        if (Get-Process RocketLeague -ErrorAction SilentlyContinue) {
            Write-Warn "Rocket League is running. Fully exit it - the .ini is only read at launch."
        }
    }
}

# ── 7. Shortcuts ────────────────────────────────────────────────────────────
Write-Step "Creating shortcuts"
$startBat = Join-Path $installDir "start.bat"
$icon = Join-Path $installDir "assets\icon.ico"
$wsh = New-Object -ComObject WScript.Shell

function New-Shortcut($lnkPath, $targetPath, $iconPath) {
    $sc = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath = $targetPath
    $sc.WorkingDirectory = (Split-Path $targetPath -Parent)
    $sc.WindowStyle = 7  # minimised
    if ($iconPath -and (Test-Path $iconPath)) { $sc.IconLocation = $iconPath }
    $sc.Save()
}

$startMenuLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Rocket League H2H.lnk"
New-Shortcut $startMenuLnk $startBat $icon
Write-Ok "Start Menu shortcut created"

if (Confirm-Yes "Add a Desktop shortcut too?") {
    $desktopLnk = Join-Path $env:USERPROFILE "Desktop\Rocket League H2H.lnk"
    New-Shortcut $desktopLnk $startBat $icon
    Write-Ok "Desktop shortcut created"
}

# ── 8. Final ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Done!" -ForegroundColor Green
Write-Host "  ----" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  One thing the installer can't do for you:" -ForegroundColor White
Write-Host "  Set Rocket League to BORDERLESS in Settings > Video > Display Mode." -ForegroundColor White
Write-Host "  The overlay can't render over true fullscreen DirectX." -ForegroundColor DarkGray
Write-Host ""

if (Confirm-Yes "Launch Rocket League H2H now?") {
    Start-Process -FilePath $startBat
    Write-Ok "Launched. Look for the H2H icon in the system tray (bottom-right)."
}
