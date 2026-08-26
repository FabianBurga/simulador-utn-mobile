@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo P2-A.1 - NORMALIZACION SEGURA DE HABILIDADES
echo ============================================================
echo.

echo [1/7] Verificando baseline P0...
python tools\validate_project.py
if errorlevel 1 goto ERROR


echo.
echo [2/7] Verificando sintaxis de scripts P2-A.1...
python -m py_compile ^
tools\apply_p2a1_normalization.py ^
tools\validate_p2a1.py ^
tools\restore_p2a_checkpoint.py

if errorlevel 1 goto ERROR

echo [OK] Scripts P2-A.1 compilan correctamente.


echo.
echo [3/7] Aplicando normalizacion P2-A.1...
python tools\apply_p2a1_normalization.py
if errorlevel 1 goto RESTORE


echo.
echo [4/7] Validando P2-A.1...
python tools\validate_p2a1.py
if errorlevel 1 goto RESTORE


echo.
echo [5/7] Verificando compatibilidad con P2-A...
python tools\validate_p2a.py
if errorlevel 1 goto RESTORE


echo.
echo [6/7] Ejecutando regresion completa P0...
python tools\validate_project.py
if errorlevel 1 goto RESTORE


echo.
echo [7/7] Ejecutando auditoria P1...
python tools\audit_bank_p1.py
if errorlevel 1 goto RESTORE


echo.
echo ============================================================
echo P2-A.1 APROBADO
echo ============================================================
echo Habilidades normalizadas.
echo P0, P1 y P2-A permanecen compatibles.
echo.
exit /b 0


:RESTORE

echo.
echo ============================================================
echo ERROR DURANTE P2-A.1
echo ============================================================
echo.
echo Ejecutando restauracion segura desde checkpoint P2-A...

python tools\restore_p2a_checkpoint.py

if errorlevel 1 (
    echo.
    echo [ERROR CRITICO] La restauracion SHA-256 fallo.
    echo NO MODIFICAR EL BANCO HASTA REVISARLO.
    echo.
    exit /b 2
)

echo.
echo Verificando semanticamente el checkpoint restaurado...
python tools\validate_p2a.py

if errorlevel 1 (
    echo.
    echo [ERROR CRITICO] El archivo coincide con el backup,
    echo pero no supera validate_p2a.py.
    echo.
    exit /b 2
)

echo.
echo [OK] Rollback verificado por SHA-256 y por P2-A.
goto ERROR


:ERROR

echo.
echo ============================================================
echo P2-A.1 RECHAZADO
echo NO CONTINUAR CON EL SIGUIENTE PARCHE
echo ============================================================
echo.
exit /b 1