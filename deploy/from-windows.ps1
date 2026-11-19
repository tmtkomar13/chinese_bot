param([string]$Destination = 'tmtkomar@HomeServer', [switch]$CheckTraining)
$ErrorActionPreference = 'Stop'
if ($Destination -notmatch '^[a-zA-Z0-9_.-]+@[a-zA-Z0-9][a-zA-Z0-9.-]*$') {
    throw 'Expected user@host'
}
$project = Split-Path -Parent $PSScriptRoot
if ($CheckTraining) {
    $verifier = Join-Path $PSScriptRoot 'check-live.py'
    $payload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($verifier))
    ssh -t $Destination "printf %s $payload | base64 -d | sudo python3 -u -"
    exit $LASTEXITCODE
}
$name = 'trainer-' + [guid]::NewGuid().ToString('N') + '.tar.gz'
$bundle = Join-Path ([System.IO.Path]::GetTempPath()) $name
try {
    tar.exe -czf $bundle --exclude=__pycache__ -C $project trainer deploy data/trainer.sqlite3 data/vocabulary.tsv requirements.txt README.md PROJECT_STRUCTURE.md
    if ($LASTEXITCODE -ne 0) { throw 'Archive creation failed' }
    scp $bundle "${Destination}:$name"
    if ($LASTEXITCODE -ne 0) { throw 'SCP failed' }
    # A terminal is required for sudo and the hidden VK token prompt.
    $remote = 'set -e; dir=$(mktemp -d); tar -xzf {0} -C "$dir"; cd "$dir"; sudo bash deploy/install.sh' -f $name
    ssh -t $Destination $remote
    if ($LASTEXITCODE -ne 0) { throw 'Debian installation did not finish' }
} finally {
    if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle }
}
