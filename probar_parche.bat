@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo SIMULADOR UTN - VALIDACION AUTOMATICA
echo ============================================================
echo.

python tools\validate_project.py

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% EQU 0 (
    echo ============================================================
    echo PARCHE APROBADO
    echo ============================================================
) else (
    echo ============================================================
    echo PARCHE RECHAZADO - EXISTEN ERRORES
    echo ============================================================
)

echo.

exit /b %EXITCODE%