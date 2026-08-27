# P2-M6 — Student Access & Enrollment

## Objetivo

Cambiar únicamente la puerta de entrada al simulador. El motor académico P2, M5A Analytics y M5B Master Dashboard permanecen congelados.

## Flujo V1

1. El administrador crea una cohorte y un código de invitación.
2. El estudiante abre `Crear cuenta`.
3. Ingresa código de acceso, nombre/alias y PIN de 6 dígitos.
4. El sistema genera un código `UTN-XXXXXX`.
5. El sistema genera un código de recuperación de 16 caracteres y lo muestra una sola vez.
6. El estudiante inicia sesión con código UTN + PIN.
7. Cinco intentos PIN fallidos producen bloqueo de 15 minutos.
8. Recuperación rota el código de recuperación después de usarlo.
9. El administrador puede suspender/reactivar/restablecer acceso sin conocer el PIN.

## Privacidad

No son obligatorios correo, teléfono, cédula, IP, GPS ni fingerprinting.
El PIN y el código de recuperación nunca se guardan en texto plano.

## Capas

- `p2_mobile_users`: credencial académica existente.
- `p2_student_profiles`: nombre/alias, cohorte, estado.
- `p2_access_security`: lockout + recuperación.
- `p2_cohorts`: grupos.
- `p2_registration_invites`: invitaciones.
- `p2_invite_redemptions`: uso de invitaciones.
- `p2_access_events`: auditoría de acceso independiente de M5A.

## Roadmap

- M6.1 Contract Freeze
- M6.2 Database Foundation
- M6.3 Student Access Engine
- M6.4 Registration + Login UI
- M6.5 PIN Recovery
- M6.6 Master Dashboard Access Management
- M6.7 Access Analytics
- M6.8 Cloud QA + Freeze
