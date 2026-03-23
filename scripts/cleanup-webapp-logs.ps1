# Clean up old webapp_logs directories and zip files
# Keeps only the last 2 most recent ones

param(
    [int]$Keep = 2,
    [switch]$DryRun
)

$workspaceRoot = Split-Path -Parent $PSScriptRoot
Set-Location $workspaceRoot

Write-Host "🧹 Webapp Logs Cleanup Script" -ForegroundColor Cyan
Write-Host "=" * 80

# Find all webapp_logs directories and zip files
$logDirs = Get-ChildItem -Directory -Filter "webapp_logs*" | Sort-Object LastWriteTime -Descending
$logZips = Get-ChildItem -File -Filter "webapp_logs*.zip" | Sort-Object LastWriteTime -Descending

$totalDirs = $logDirs.Count
$totalZips = $logZips.Count

Write-Host "`n📁 Found $totalDirs webapp_logs directories"
Write-Host "📦 Found $totalZips webapp_logs zip files"

if ($totalDirs -eq 0 -and $totalZips -eq 0) {
    Write-Host "`n✅ No webapp_logs files found. Nothing to clean up." -ForegroundColor Green
    exit 0
}

Write-Host "`n📋 Current files (newest to oldest):"
Write-Host ("-" * 80)

# Display directories
if ($totalDirs -gt 0) {
    Write-Host "`nDirectories:" -ForegroundColor Yellow
    foreach ($dir in $logDirs) {
        $size = (Get-ChildItem -Path $dir.FullName -Recurse -File | Measure-Object -Property Length -Sum).Sum
        $sizeStr = if ($size -gt 1MB) { "{0:N2} MB" -f ($size / 1MB) } else { "{0:N2} KB" -f ($size / 1KB) }
        $status = if ($logDirs.IndexOf($dir) -lt $Keep) { "KEEP" } else { "DELETE" }
        $color = if ($status -eq "KEEP") { "Green" } else { "Red" }
        
        Write-Host "  [$status] " -NoNewline -ForegroundColor $color
        Write-Host "$($dir.Name) - $sizeStr - Modified: $($dir.LastWriteTime)"
    }
}

# Display zip files
if ($totalZips -gt 0) {
    Write-Host "`nZip Files:" -ForegroundColor Yellow
    foreach ($zip in $logZips) {
        $sizeStr = if ($zip.Length -gt 1MB) { "{0:N2} MB" -f ($zip.Length / 1MB) } else { "{0:N2} KB" -f ($zip.Length / 1KB) }
        $status = if ($logZips.IndexOf($zip) -lt $Keep) { "KEEP" } else { "DELETE" }
        $color = if ($status -eq "KEEP") { "Green" } else { "Red" }
        
        Write-Host "  [$status] " -NoNewline -ForegroundColor $color
        Write-Host "$($zip.Name) - $sizeStr - Modified: $($zip.LastWriteTime)"
    }
}

# Calculate what will be removed
$dirsToRemove = $logDirs | Select-Object -Skip $Keep
$zipsToRemove = $logZips | Select-Object -Skip $Keep

$totalToRemove = $dirsToRemove.Count + $zipsToRemove.Count

if ($totalToRemove -eq 0) {
    Write-Host "`n✅ All files are within the keep limit ($Keep). Nothing to remove." -ForegroundColor Green
    exit 0
}

Write-Host "`n📊 Summary:"
Write-Host "  • Keeping: $($Keep) most recent of each type"
Write-Host "  • Removing: $($totalToRemove) items ($($dirsToRemove.Count) directories + $($zipsToRemove.Count) zip files)" -ForegroundColor Yellow

if ($DryRun) {
    Write-Host "`n🔍 DRY RUN MODE - No files will be deleted" -ForegroundColor Cyan
    Write-Host "   Remove -DryRun flag to actually delete files"
    exit 0
}

# Confirm deletion
Write-Host ""
$confirmation = Read-Host "Do you want to proceed with deletion? (yes/no)"

if ($confirmation -ne "yes") {
    Write-Host "`n❌ Cleanup cancelled" -ForegroundColor Yellow
    exit 0
}

Write-Host "`n🗑️  Removing old files..." -ForegroundColor Red
$removed = 0

# Remove old directories
foreach ($dir in $dirsToRemove) {
    try {
        Remove-Item -Path $dir.FullName -Recurse -Force
        Write-Host "  ✓ Removed directory: $($dir.Name)" -ForegroundColor Green
        $removed++
    }
    catch {
        Write-Host "  ✗ Failed to remove $($dir.Name): $_" -ForegroundColor Red
    }
}

# Remove old zip files
foreach ($zip in $zipsToRemove) {
    try {
        Remove-Item -Path $zip.FullName -Force
        Write-Host "  ✓ Removed zip: $($zip.Name)" -ForegroundColor Green
        $removed++
    }
    catch {
        Write-Host "  ✗ Failed to remove $($zip.Name): $_" -ForegroundColor Red
    }
}

Write-Host "`n✅ Cleanup complete! Removed $removed items." -ForegroundColor Green
Write-Host ""
Write-Host "💡 Tip: These files are now in .gitignore and won't be tracked by Git." -ForegroundColor Cyan
