$ErrorActionPreference = "Stop"
$env:P2_MOBILE_LOCAL = "1"

$ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254*" -and
        $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL|Virtual"
    } |
    Sort-Object InterfaceMetric |
    Select-Object -First 1 -ExpandProperty IPAddress

if (-not $ip) {
    $ip = "IP-DE-TU-PC"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "P2 MOBILE - LOCAL PHONE SMOKE"
Write-Host "============================================================"
Write-Host "PC:      http://localhost:8501"
Write-Host "TELEFONO: http://${ip}:8501"
Write-Host ""
Write-Host "El telefono debe estar conectado a la misma red Wi-Fi."
Write-Host "Usa dos codigos distintos para probar aislamiento."
Write-Host "Ejemplo: TESTA / 1234 y TESTB / 5678"
Write-Host "============================================================"
Write-Host ""

python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501

Remove-Item Env:P2_MOBILE_LOCAL -ErrorAction SilentlyContinue
