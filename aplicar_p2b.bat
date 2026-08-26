@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo P2-B - MAPEO OFICIAL UTN/FICA 2P2026
echo ============================================================
echo.

echo [1/8] Verificando baseline P0...
python tools\validate_project.py
if errorlevel 1 goto ERROR


echo.
echo [2/8] Verificando checkpoint P2-A.1...
python tools\validate_p2a1.py
if errorlevel 1 goto ERROR


echo.
echo [3/8] Verificando sintaxis de scripts P2-B...
python -m py_compile ^
tools\apply_p2b_utn_mapping.py ^
tools\validate_p2b.py ^
tools\restore_p2a1_checkpoint.py

if errorlevel 1 goto ERROR

echo [OK] Scripts P2-B compilan correctamente.


echo.
echo [4/8] Aplicando mapeo oficial UTN/FICA...
python tools\apply_p2b_utn_mapping.py
if errorlevel 1 goto RESTORE


echo.
echo [5/8] Validando P2-B...
python tools\validate_p2b.py
if errorlevel 1 goto RESTORE


echo.
echo [6/8] Verificando compatibilidad con P2-A...
python tools\validate_p2a.py
if errorlevel 1 goto RESTORE


echo.
echo [7/8] Ejecutando regresion completa P0...
python tools\validate_project.py
if errorlevel 1 goto RESTORE


echo.
echo [8/8] Ejecutando auditoria P1...
python tools\audit_bank_p1.py
if errorlevel 1 goto RESTORE


echo.
echo ============================================================
echo P2-B APROBADO
echo ============================================================
echo Mapeo oficial UTN/FICA 2P2026 incorporado.
echo Baseline P0, calidad P1 y taxonomia P2 permanecen compatibles.
echo.
exit /b 0



:RESTORE

echo.
echo ============================================================
echo ERROR DURANTE P2-B
echo ============================================================
echo.
echo Restaurando checkpoint P2-A.1 mediante SHA-256...

python tools\restore_p2a1_checkpoint.py

if errorlevel 1 (
    echo.
    echo [ERROR CRITICO] Fallo la restauracion SHA-256.
    echo NO MODIFICAR EL BANCO HASTA REVISARLO.
    echo.
    exit /b 2
)

echo.
echo Verificando checkpoint restaurado con P2-A.1...
python tools\validate_p2a1.py

if errorlevel 1 (
    echo.
    echo [ERROR CRITICO] El archivo coincide con el backup,
    echo pero no supera validate_p2a1.py.
    echo.
    exit /b 2
)

echo.
echo [OK] Rollback P2-A.1 verificado por SHA-256 y semantica.
goto ERROR



:ERROR

echo.
echo ============================================================
echo P2-B RECHAZADO
echo NO CONTINUAR CON EL SIGUIENTE PARCHE
echo ============================================================
echo.
exit /b 1