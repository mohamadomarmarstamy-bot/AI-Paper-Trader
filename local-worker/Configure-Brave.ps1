$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path config.json)) { throw 'Existing config.json missing. Keep your paired worker configuration.' }
$jarvisConfig = Get-Content config.json -Raw | ConvertFrom-Json
$braveSecret = Read-Host 'Paste your Brave Search API key here (hidden)' -AsSecureString
$bravePointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($braveSecret)
try {
    $braveText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bravePointer).Trim()
    if ([string]::IsNullOrWhiteSpace($braveText)) { throw 'No API key entered; config not changed.' }
    $jarvisConfig.search_provider = 'brave'
    $jarvisConfig.sessions_per_day = 30
    $jarvisConfig.brave_monthly_limit = 900
    $jarvisConfig.brave_api_key = $braveText
    $jarvisConfig | ConvertTo-Json -Depth 10 | Set-Content config.json -Encoding UTF8
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bravePointer)
    $braveText = $null
    $braveSecret.Dispose()
}
Write-Host 'Saved Brave Search, up to 30 sessions daily, 900 searches monthly. Existing app token preserved.'
Write-Host 'No search was made. Restart the worker to load this configuration. Keep config.json private.'
