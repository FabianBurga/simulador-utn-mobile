# P2-M5B — Master Dashboard

El panel administrativo es una app Streamlit separada del simulador de estudiantes.

## Archivos
- `app.py`: simulador público.
- `admin_app.py`: panel maestro privado.
- `admin_backend.py`: acceso a vistas/tablas analíticas.

## Seguridad
No agregue el panel al menú del estudiante.
Use un despliegue Streamlit separado con `admin_app.py` como Main file path.

## Streamlit Secrets del panel

Copie el hash generado en `.admin_setup/admin_credentials.txt`.

```toml
[supabase]
url = "https://...supabase.co"
secret_key = "SU_SECRET_EXISTENTE"

[admin]
password_hash = "pbkdf2_sha256$..."
```

La `secret_key` nunca se sube a GitHub.

## Despliegue
1. Streamlit Community Cloud: Create app.
2. Use el mismo repositorio Mobile.
3. Main file path: `admin_app.py`.
4. Pegue los Secrets del panel.
5. Deploy.

## Vistas Supabase
- `p2_admin_live_sessions`
- `p2_admin_attempts`
- `p2_admin_student_summary`
- `p2_admin_daily_metrics`
- `p2_admin_question_metrics`
- `p2_admin_retention`
- `p2_admin_funnel`
- `p2_admin_system_health`

Todas están restringidas al `service_role`.
