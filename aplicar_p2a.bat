@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================================
echo P2-A - TAXONOMIA PEDAGOGICA SEGURA
echo ============================================================
echo.

echo [1/5] Verificando baseline P0...
python tools\validate_project.py

if errorlevel 1 goto ERROR

echo.
echo [2/5] Aplicando taxonomia P2-A...
python tools\apply_p2a_taxonomy.py

if errorlevel 1 goto RESTORE

echo.
echo [3/5] Validando taxonomia P2-A...
python tools\validate_p2a.py

if errorlevel 1 goto RESTORE

echo.
echo [4/5] Repitiendo pruebas P0...
python tools\validate_project.py

if errorlevel 1 goto RESTORE

echo.
echo [5/5] Verificando auditoria P1...
python tools\audit_bank_p1.py

if errorlevel 1 goto RESTORE

echo.
echo ============================================================
echo P2-A APROBADO
echo ============================================================
echo Taxonomia agregada sin alterar el comportamiento anterior.
echo.
exit /b 0


:RESTORE
echo.
echo ERROR DURANTE P2-A.
echo Restaurando question_bank.json desde checkpoint P1...
copy /Y ^
"data\question_bank_p1_backup.json" ^
"data\question_bank.json" >nul

echo Banco restaurado al checkpoint P1.
goto ERROR


:ERROR
echo.
echo ============================================================
echo P2-A RECHAZADO
echo NO CONTINUAR CON EL SIGUIENTE PARCHE
echo ============================================================
echo.
exit /b 1