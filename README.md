# Simulador UTN Interactivo - FICA/FICAYA

Aplicación local de práctica construida a partir de los dos simulacros PDF incluidos en `sources/`.

## Inicio rápido en Windows
1. Instale Python 3.11 o 3.12 y Visual Studio Code.
2. Descomprima esta carpeta.
3. Abra la carpeta completa en VS Code.
4. Haga doble clic en `iniciar_windows.bat` o ejecútelo desde la terminal.
5. El navegador abrirá el simulador local.

## Inicio desde terminal
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

En macOS/Linux cambie la activación por `source .venv/bin/activate`.

## Funciones
- Simulacro completo de 90 preguntas y 90 minutos.
- Práctica sin límite de tiempo.
- Examen rápido y práctica por área.
- Navegación anterior/siguiente y panel de preguntas.
- Guardado automático de respuestas en la sesión.
- Corrección, puntaje global y por área.
- Explicación/criterio de resolución.
- Preguntas problemáticas del material original marcadas como `REVISAR FUENTE`.
- Visualización de la página original del PDF para conservar diagramas, fórmulas y tablas.
- Historial local de intentos en `results/history.json`.

## Importante
Las claves se resolvieron de forma independiente a partir de los enunciados. Algunas preguntas del material fuente presentan opciones duplicadas, errores de redacción o resultados que no coinciden exactamente con ninguna opción. Esas preguntas aparecen marcadas en el simulador para evitar enseñar una clave falsa como si fuera segura.
