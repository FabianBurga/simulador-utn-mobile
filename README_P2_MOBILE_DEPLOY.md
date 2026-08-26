# P2 Mobile RC1

## Objetivo
Una copia independiente del P2 FROZEN, preparada para telefono, multiusuario y nube.

## Arquitectura
- Streamlit: interfaz publica.
- Supabase: historial + student_state por estudiante.
- Codigo de estudiante + PIN numerico: acceso simple de piloto.
- history.json y student_state.json se mantienen privados por estudiante en cache temporal.

## Smoke local
Desde esta carpeta en PowerShell:

    .\M2_START_LOCAL_PHONE_SMOKE.ps1

El script mostrara una URL LAN del tipo http://192.168.x.x:8501.
El telefono debe estar en la misma red Wi-Fi.

## Produccion
1. Crear un proyecto Supabase.
2. Ejecutar `SUPABASE_SCHEMA.sql` una sola vez en SQL Editor.
3. Crear repositorio GitHub con ESTA carpeta Mobile.
4. Desplegar `app.py` en Streamlit Community Cloud.
5. En Streamlit > App settings > Secrets pegar:

    [mobile]
    backend = "supabase"

    [supabase]
    url = "https://...supabase.co"
    secret_key = "..."

Nunca subir el secret real a GitHub.

## Alcance de seguridad RC1
Este login codigo+PIN es adecuado para un piloto educativo sencillo.
No usar datos personales en el codigo de estudiante.
Para una version institucional posterior conviene migrar a autenticacion formal.
