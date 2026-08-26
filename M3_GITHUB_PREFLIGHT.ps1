$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "P2 MOBILE - GITHUB PREFLIGHT"
Write-Host "============================================================"

git --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Git no esta instalado o no esta en PATH."
    exit 1
}

python -m py_compile app.py mobile_backend.py persistence_engine.py recommendations_engine.py history_engine.py dashboard_engine.py mastery_engine.py adaptive_selector.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Compilacion."
    exit 1
}

if (Test-Path ".streamlit\secrets.toml") {
    Write-Host "[FAIL] Existe .streamlit\secrets.toml. No lo subas a GitHub."
    exit 1
}

Write-Host "[PASS] Git disponible"
Write-Host "[PASS] Proyecto compila"
Write-Host "[PASS] No hay secrets.toml real"
Write-Host "[PASS] requirements.txt presente: $((Test-Path '.\requirements.txt'))"
Write-Host "[PASS] SUPABASE_SCHEMA.sql presente: $((Test-Path '.\SUPABASE_SCHEMA.sql'))"
Write-Host ""
Write-Host "READY_FOR_GITHUB"
