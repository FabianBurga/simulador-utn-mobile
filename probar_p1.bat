@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo P1 - VALIDACION Y AUDITORIA DEL BANCO
echo ============================================================
echo.

echo [1/2] Ejecutando baseline P0...
python tools\validate_project.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo P1 CANCELADO - EL BASELINE P0 HA FALLADO
    echo ============================================================
    exit /b 1
)

echo.
echo [2/2] Ejecutando auditoria P1...
python tools\audit_bank_p1.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo P1 RECHAZADO
    echo ============================================================
    exit /b 1
)

echo.
echo ============================================================
echo P1-A APROBADO
echo ============================================================

exit /b 0