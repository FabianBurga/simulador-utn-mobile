from __future__ import annotations
import json
import random

from adaptive_selector import (
    load_adaptive_context,
    select_adaptive_questions,
)
# P2-E2B ADAPTIVE APP INTEGRATION
import time
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st
from analytics_integration import (
    analytics_abandon_attempt,
    analytics_answer_selected,
    analytics_complete_attempt,
    analytics_logout,
    analytics_page_view,
    analytics_question_view,
    analytics_set_flag,
    analytics_start_attempt,
    ensure_analytics,
)
from mobile_backend import (
    mobile_bootstrap,
    mobile_push_user_files,
    mobile_sidebar_account,
)
from dashboard_engine import (
    build_dashboard_model,
    render_dashboard,
)
from history_engine import render_history_screen
from recommendations_engine import (
    build_recommendations_model,
    render_recommendations,
)
from persistence_engine import (
    sync_persistent_state,
)

from content_manager import load_runtime_question_bank

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / 'data' / 'question_bank.json'
REINFORCEMENT_FILE = BASE / 'data' / 'reinforcement_bank.json'
SOURCES = BASE / 'sources'
RESULTS = BASE / 'results'
HISTORY = RESULTS / 'history.json'
RESULTS.mkdir(exist_ok=True)

st.set_page_config(page_title='Simulador UTN Interactivo', page_icon='🎓', layout='wide')

CSS = r'''
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
[data-testid="stMetricValue"] {font-size: 1.55rem;}
.question-chip {display:inline-block; padding:.3rem .55rem; border-radius:.55rem; margin:.1rem; border:1px solid #aaa; font-size:.85rem;}
.badge-warn {padding:.45rem .7rem; border-radius:.5rem; background:#fff3cd; color:#664d03; margin-bottom:.7rem;}
.badge-ok {padding:.45rem .7rem; border-radius:.5rem; background:#d1e7dd; color:#0f5132; margin-bottom:.7rem;}
.source-note {font-size:.92rem; opacity:.9;}
</style>
'''
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_data(ttl=2)
def load_bank():
    # P2-E0 EXTENSIBLE CONTENT LOADER
    return load_runtime_question_bank(DATA_FILE)


CONTENT_MODE_ORDER = [
    'Examen completo',
    'Práctica',
    'Examen rápido',
    'Por área',
]


def question_allowed_modes(q):
    raw = q.get('allowed_modes')

    if raw is None:
        # Banco legado: conserva las cuatro modalidades históricas.
        return list(CONTENT_MODE_ORDER)

    if not isinstance(raw, list):
        return []

    return [
        mode
        for mode in CONTENT_MODE_ORDER
        if mode in raw
    ]


def available_modes_for_source(bank, source):
    selected = (
        list(bank)
        if source.startswith('Mixto')
        else [
            q
            for q in bank
            if q.get('simulacro') == source
        ]
    )

    allowed = {
        mode
        for q in selected
        for mode in question_allowed_modes(q)
    }

    return [
        mode
        for mode in CONTENT_MODE_ORDER
        if mode in allowed
    ]


def is_reinforcement_question(q):
    return (
        q.get('_practice_origin') == 'reinforcement'
        or q.get('origin') == 'generated_reinforcement'
    )


def prepare_reinforcement_question(raw):
    q = dict(raw)

    if q.get('quality', {}).get('status') != 'validated':
        return None

    if q.get('quality', {}).get('mastery_eligible') is not True:
        return None

    if q.get('official_utn_question') is not False:
        return None

    q['_practice_origin'] = 'reinforcement'
    q['simulacro'] = 'Refuerzo FICA'

    raw_id = str(q.get('id', 'R-000'))

    try:
        q['number'] = int(raw_id.split('-')[-1])
    except (TypeError, ValueError):
        q['number'] = raw_id

    q['subject'] = q.get('area', 'Refuerzo FICA')
    q['status'] = 'refuerzo_validado'

    q['source_note'] = (
        q.get('source_note')
        or (
            'Material de refuerzo generado para preparación FICA. '
            'No es una pregunta oficial de admisión UTN.'
        )
    )

    return q


@st.cache_data
def load_reinforcement_bank():
    if not REINFORCEMENT_FILE.exists():
        return []

    try:
        payload = json.loads(
            REINFORCEMENT_FILE.read_text(
                encoding='utf-8'
            )
        )
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []

    if payload.get('official_utn_question_bank') is not False:
        return []

    prepared = []

    for raw in payload.get('items', []):
        if not isinstance(raw, dict):
            continue

        q = prepare_reinforcement_question(raw)

        if q is not None:
            prepared.append(q)

    return prepared


def question_choice_data(q):
    raw = q.get('choices', [])

    # Caso 1:
    # {"A": "texto", "B": "texto", ...}
    if isinstance(raw, dict):
        keys = [
            str(key)
            for key in raw.keys()
        ]

        labels = {
            str(key): str(value)
            for key, value in raw.items()
        }

        return keys, labels

    # Caso 2:
    # [{"id": "A", "text": "..."}, ...]
    if (
        isinstance(raw, list)
        and raw
        and all(
            isinstance(item, dict)
            for item in raw
        )
    ):
        keys = []
        labels = {}

        for index, item in enumerate(raw):
            key = str(
                item.get('id')
                or item.get('key')
                or item.get('label')
                or chr(65 + index)
            )

            value = (
                item.get('text')
                or item.get('value')
                or item.get('content')
                or key
            )

            keys.append(key)
            labels[key] = str(value)

        return keys, labels

    # Caso 3:
    # Lista tradicional.
    if isinstance(raw, list):
        values = [
            str(value)
            for value in raw
        ]

        # Banco original:
        # ["A", "B", "C", "D", "E"]
        if q.get('answer') in values:
            return (
                values,
                {
                    value: value
                    for value in values
                },
            )

        # Refuerzo:
        # ["texto A", "texto B", ...]
        keys = [
            chr(65 + index)
            for index in range(
                len(values)
            )
        ]

        return (
            keys,
            dict(
                zip(
                    keys,
                    values,
                )
            ),
        )

    return [], {}



def canonical_answer_key(value):
    """Normaliza A, a, A), A. o A: texto a una clave comparable."""
    text = str(value or "").strip()

    if not text:
        return ""

    if len(text) == 1 and text.isalpha():
        return text.upper()

    if (
        len(text) >= 2
        and text[0].isalpha()
        and (
            text[1].isspace()
            or text[1] in ").:-"
        )
    ):
        return text[0].upper()

    return text.upper()

def choice_display_text(key, labels):
    text = labels.get(
        key,
        key,
    )

    if text == key:
        return key

    stripped = text.strip()

    prefixes = (
        f'{key}.' ,
        f'{key})',
        f'{key}:',
        f'{key} -',
    )

    if stripped.startswith(prefixes):
        return stripped

    return f'{key}. {stripped}'



def render_original_explanation_base(q):
    st.markdown(
        '#### Cómo resolverlo'
    )

    explanation = q.get(
        'explanation'
    )

    if explanation:
        st.write(
            explanation
        )
    else:
        st.info(
            'Esta pregunta no tiene una '
            'explicación desarrollada disponible.'
        )

    if q.get(
        'source_note'
    ):
        st.warning(
            q['source_note']
        )

    # --------------------------------------------------------
    # P2-D5.1
    #
    # El panel ampliado NO inventa una nueva solución.
    # Expone la taxonomía pedagógica y la alineación
    # FICA/UTN ya almacenadas en question_bank.json.
    # --------------------------------------------------------

    topic = q.get(
        'topic'
    )

    skill = q.get(
        'skill'
    )

    subskill = q.get(
        'subskill'
    )

    taxonomy_status = q.get(
        'taxonomy_status'
    )

    official = q.get(
        'utn_official',
        {}
    )

    quality = q.get(
        'quality',
        {}
    )

    with st.expander(
        'Ver explicación ampliada'
    ):
        st.markdown(
            '##### Qué estás practicando'
        )

        pedagogical_data = []

        if q.get(
            'area'
        ):
            pedagogical_data.append(
                (
                    'Área',
                    q.get(
                        'area'
                    ),
                )
            )

        if topic:
            pedagogical_data.append(
                (
                    'Tema',
                    topic,
                )
            )

        if skill:
            pedagogical_data.append(
                (
                    'Habilidad',
                    skill,
                )
            )

        if subskill:
            pedagogical_data.append(
                (
                    'Subhabilidad',
                    subskill,
                )
            )

        if pedagogical_data:
            for label, value in pedagogical_data:
                st.markdown(
                    f'**{label}:** {value}'
                )
        else:
            st.caption(
                'No hay taxonomía pedagógica '
                'adicional disponible para esta pregunta.'
            )

        # ----------------------------------------------------
        # ALINEACIÓN OFICIAL
        # ----------------------------------------------------

        if isinstance(
            official,
            dict,
        ) and official:
            st.markdown(
                '##### Alineación con el temario FICA'
            )

            official_area = official.get(
                'area'
            )

            official_unit = official.get(
                'unit'
            )

            official_topic = official.get(
                'topic'
            )

            alignment = official.get(
                'alignment'
            )

            confidence = official.get(
                'confidence'
            )

            if official_area:
                st.markdown(
                    f'**Área oficial:** {official_area}'
                )

            if official_unit:
                st.markdown(
                    f'**Unidad oficial:** {official_unit}'
                )

            if official_topic:
                st.markdown(
                    f'**Contenido oficial:** {official_topic}'
                )

            if alignment:
                alignment_labels = {
                    'exact':
                        'Coincidencia directa',

                    'related':
                        'Contenido relacionado',

                    'out_of_scope':
                        'Fuera del temario FICA actual',

                    'uncertain':
                        'Alineación pendiente de revisión',
                }

                alignment_text = alignment_labels.get(
                    alignment,
                    alignment,
                )

                st.markdown(
                    f'**Tipo de alineación:** '
                    f'{alignment_text}'
                )

            if confidence:
                confidence_labels = {
                    'high':
                        'alta',

                    'medium':
                        'media',

                    'low':
                        'baja',
                }

                st.markdown(
                    '**Confianza del mapeo:** '
                    + confidence_labels.get(
                        confidence,
                        confidence,
                    )
                )

            if (
                official.get(
                    'coverage_eligible'
                )
                is False
            ):
                st.caption(
                    'Esta pregunta no se utiliza '
                    'para medir cobertura oficial.'
                )

        # ----------------------------------------------------
        # ESTADO DE LA PREGUNTA
        # ----------------------------------------------------

        st.markdown(
            '##### Estado de la explicación'
        )

        if (
            quality.get(
                'short_explanation'
            )
            is True
        ):
            st.caption(
                'La explicación disponible en el '
                'banco original es breve. '
                'Este panel amplía el contexto de estudio, '
                'pero no inventa pasos que no hayan sido '
                'validados todavía.'
            )
        else:
            st.caption(
                'La resolución mostrada arriba proviene '
                'del banco original validado. '
                'Este panel añade contexto pedagógico '
                'y curricular.'
            )

        if (
            taxonomy_status
            == 'source_review'
            or
            q.get(
                'status'
            )
            == 'revisar_fuente'
        ):
            st.warning(
                'Esta pregunta conserva una marca '
                'de revisión de fuente. No debe '
                'interpretarse como evidencia de dominio '
                'hasta resolver esa revisión.'
            )



# ============================================================
# P2-D5.6 ORIGINAL PEDAGOGICAL EXPLANATIONS
#
# Companion bank independiente:
# - solo originales VALIDATED
# - solo mastery_eligible=True
# - las source_review quedan fuera
# - no modifica question_bank
# - no altera Evaluación
# ============================================================

@st.cache_data
def load_validated_original_explanations():
    path = (
        BASE
        / 'data'
        / 'original_explanations_bank.json'
    )

    if not path.exists():
        return {}

    try:
        payload = json.loads(
            path.read_text(
                encoding='utf-8'
            )
        )
    except Exception:
        return {}

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    if (
        payload.get(
            'schema_version'
        )
        != 'p2d5_2_original_explanation_v1'
    ):
        return {}

    if (
        payload.get(
            'bank_version'
        )
        != 'p2d5_2_original_explanation_bank_v1'
    ):
        return {}

    items = payload.get(
        'items',
        [],
    )

    if (
        not isinstance(
            items,
            list,
        )
        or
        len(
            items
        )
        != 180
    ):
        return {}

    verification_fields = (
        'content_verified',
        'answer_verified',
        'steps_verified',
        'distractors_verified',
        'pedagogical_reviewed',
    )

    validated = {}

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        source_id = item.get(
            'source_question_id'
        )

        quality = item.get(
            'quality',
            {},
        )

        if not source_id:
            continue

        if not isinstance(
            quality,
            dict,
        ):
            continue

        if (
            quality.get(
                'status'
            )
            != 'validated'
        ):
            continue

        if (
            quality.get(
                'mastery_eligible'
            )
            is not True
        ):
            continue

        if not all(
            quality.get(
                field
            )
            is True
            for field
            in verification_fields
        ):
            continue

        if (
            'source_review_required'
            in quality.get(
                'flags',
                [],
            )
        ):
            continue

        if (
            item.get(
                'official_utn_question'
            )
            is not False
        ):
            continue

        validated[
            source_id
        ] = item

    if len(
        validated
    ) != 173:
        return {}

    return validated


# P2-E0.5 EXTENSION PEDAGOGICAL COMPANIONS
@st.cache_data
def load_validated_extension_explanations():
    path = (
        BASE
        / 'data'
        / 'extension_explanations_bank.json'
    )

    if not path.exists():
        return {}

    try:
        payload = json.loads(
            path.read_text(
                encoding='utf-8'
            )
        )
    except Exception:
        return {}

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    if (
        payload.get(
            'schema_version'
        )
        != 'p2e0_5_extension_pedagogical_companion_v1'
    ):
        return {}

    if (
        payload.get(
            'bank_version'
        )
        != 'p2e0_5_extension_pedagogical_companion_bank_v1'
    ):
        return {}

    items = payload.get(
        'items',
        [],
    )

    if (
        not isinstance(
            items,
            list,
        )
        or
        len(
            items
        )
        != 224
    ):
        return {}

    validated = {}

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        source_id = item.get(
            'source_question_id'
        )
        quality = item.get(
            'quality',
            {},
        )

        if not source_id:
            continue
        if not isinstance(quality, dict):
            continue
        if quality.get('status') != 'validated':
            continue
        if quality.get('pedagogical_ready') is not True:
            continue
        if quality.get('publication_active') is not True:
            continue
        if item.get('official_utn_question') is not False:
            continue

        validated[source_id] = item

    if len(validated) != 224:
        return {}

    return validated


def render_original_pedagogical_companion(q):
    bank = (
        load_validated_original_explanations()
    )

    source_id = q.get(
        'id'
    )

    item = bank.get(
        source_id
    )

    if not item:
        item = (
            load_validated_extension_explanations()
            .get(
                source_id
            )
        )

    if not item:
        return

    with st.expander(
        'Ver resolución pedagógica completa',
        expanded=False,
    ):
        idea_key = item.get(
            'idea_key'
        )

        if idea_key:
            st.markdown(
                '##### Idea clave'
            )

            st.write(
                idea_key
            )

        what_is_asked = item.get(
            'what_is_asked'
        )

        if what_is_asked:
            st.markdown(
                '##### Qué te pide realmente'
            )

            st.write(
                what_is_asked
            )

        relevant_data = item.get(
            'relevant_data',
            [],
        )

        if (
            isinstance(
                relevant_data,
                list,
            )
            and relevant_data
        ):
            st.markdown(
                '##### Datos y pistas relevantes'
            )

            for value in relevant_data:
                if value:
                    st.markdown(
                        f'- {value}'
                    )

        concept = item.get(
            'concept_or_rule'
        )

        if concept:
            st.markdown(
                '##### Concepto o regla'
            )

            st.write(
                concept
            )

        framework = item.get(
            'formula_or_framework'
        )

        if framework:
            st.markdown(
                '##### Fórmula o marco de resolución'
            )

            st.write(
                framework
            )

        steps = item.get(
            'steps',
            [],
        )

        if (
            isinstance(
                steps,
                list,
            )
            and steps
        ):
            st.markdown(
                '##### Paso a paso'
            )

            for step in steps:
                if not isinstance(
                    step,
                    dict,
                ):
                    continue

                order = step.get(
                    'order',
                    ''
                )

                title = step.get(
                    'title',
                    ''
                )

                detail = step.get(
                    'detail',
                    ''
                )

                math_text = step.get(
                    'math'
                )

                heading = (
                    f'**{order}. {title}**'
                    if order
                    else f'**{title}**'
                )

                if title:
                    st.markdown(
                        heading
                    )

                if detail:
                    st.write(
                        detail
                    )

                if math_text:
                    st.code(
                        str(
                            math_text
                        ),
                        language=None,
                    )

        why_correct = item.get(
            'why_correct'
        )

        if why_correct:
            st.markdown(
                '##### Por qué la respuesta es correcta'
            )

            st.write(
                why_correct
            )

        distractors = item.get(
            'distractor_analysis',
            [],
        )

        if (
            isinstance(
                distractors,
                list,
            )
            and distractors
        ):
            st.markdown(
                '##### Cómo descartar las otras opciones'
            )

            for distractor in distractors:
                if not isinstance(
                    distractor,
                    dict,
                ):
                    continue

                choice = distractor.get(
                    'choice',
                    ''
                )

                why_wrong = distractor.get(
                    'why_wrong',
                    ''
                )

                error_pattern = distractor.get(
                    'error_pattern',
                    ''
                )

                if choice:
                    st.markdown(
                        f'**{choice}**'
                    )

                if why_wrong:
                    st.write(
                        why_wrong
                    )

                if error_pattern:
                    st.caption(
                        'Patrón de error: '
                        f'{error_pattern}'
                    )

        common_error = item.get(
            'common_error'
        )

        if common_error:
            st.markdown(
                '##### Error común'
            )

            st.warning(
                common_error
            )

        transfer_tip = item.get(
            'transfer_tip'
        )

        if transfer_tip:
            st.markdown(
                '##### Cómo transferirlo a otro ejercicio'
            )

            st.write(
                transfer_tip
            )

        quick_check = item.get(
            'quick_check'
        )

        if quick_check:
            st.markdown(
                '##### Comprobación rápida'
            )

            st.info(
                quick_check
            )

        st.caption(
            'Explicación pedagógica validada. '
            'El enunciado y la pregunta original '
            'permanecen sin modificaciones.'
        )


def render_original_explanation(q):
    # Conserva íntegramente el renderizador P2-D5.1.
    render_original_explanation_base(
        q
    )

    # Añade el companion únicamente cuando su estado
    # pedagógico fue validado en P2-D5.5.
    render_original_pedagogical_companion(
        q
    )

def render_reinforcement_explanation(q):
    st.markdown('#### Cómo resolverlo')
    st.write(
        q.get(
            'explanation',
            'Sin explicación disponible.'
        )
    )

    layers = q.get(
        'explanation_layers',
        {}
    )

    if not isinstance(
        layers,
        dict,
    ):
        return

    layer_order = [
        (
            'intuition',
            'Intuición rápida',
        ),
        (
            'step_by_step',
            'Paso a paso',
        ),
        (
            'common_error',
            'Error común',
        ),
        (
            'why_correct',
            'Por qué es correcta',
        ),
    ]

    available = [
        (key, title)
        for key, title in layer_order
        if layers.get(key)
    ]

    if not available:
        return

    with st.expander(
        'Ver explicación ampliada'
    ):
        for key, title in available:
            st.markdown(
                f'**{title}**'
            )

            value = layers.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                for step in value:
                    st.write(
                        f'• {step}'
                    )
            else:
                st.write(
                    value
                )


@st.cache_data(show_spinner=False)
def render_pdf_page(pdf_name: str, page_num: int, zoom: float = 1.45) -> bytes:
    path = SOURCES / pdf_name
    doc = fitz.open(path)
    page = doc.load_page(page_num - 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes('png')
    doc.close()
    return data


def load_history():
    if not HISTORY.exists():
        return []
    try:
        return json.loads(HISTORY.read_text(encoding='utf-8'))
    except Exception:
        return []


def save_history(record):
    hist = load_history()
    hist.append(record)
    HISTORY.write_text(json.dumps(hist[-100:], ensure_ascii=False, indent=2), encoding='utf-8')



    # P2-MOBILE: persistir el intento remoto inmediatamente.
    if '_mobile_ctx' in globals():
        mobile_push_user_files(
            st,
            _mobile_ctx,
            force=True,
        )

def reset_exam():
    analytics_abandon_attempt(st, reason="reset_exam")
    for k in ['exam_questions','exam_idx','answers','flags','start_ts','duration_min','submitted','exam_title','mode','last_saved_attempt','selection_strategy','adaptive_selection_meta', 'final_elapsed']:
        st.session_state.pop(k, None)



def configure_exam(bank, reinforcement_bank):
    st.title('🎓 Simulador UTN Interactivo - FICA/FICAYA')
    st.caption(
        'Entrenamiento local con bancos fuente validados y extensibles. '
        'Cada colección habilita únicamente las modalidades compatibles; '
        'el refuerzo validado sigue disponible solo en Práctica.'
    )

    c1, c2, c3 = st.columns(
        [1.2, 1.2, 1]
    )

    with c1:
        # P2-E0 DYNAMIC SIMULACRO SELECTOR
        available_sources = sorted({str(q.get('simulacro', '')).strip() for q in bank if str(q.get('simulacro', '')).strip()})
        source_options = list(available_sources)
        if len(available_sources) > 1:
            source_options.append('Mixto (todos)')
        source = st.selectbox('Banco', source_options)

        mode_options = available_modes_for_source(
            bank,
            source,
        )

        if not mode_options:
            st.error(
                'El banco seleccionado no tiene '
                'modalidades habilitadas.'
            )
            return

        mode = st.selectbox(
            'Modalidad',
            mode_options,
        )

    # --------------------------------------------------------
    # IMPORTANTE:
    # Todos los modos de evaluación parten SIEMPRE
    # de question_bank.json.
    # --------------------------------------------------------

    # P2-E0.3 DYNAMIC SOURCE + MODE FILTER
    source_subset = (
        list(bank)
        if source.startswith('Mixto')
        else [
            q
            for q in bank
            if q.get('simulacro') == source
        ]
    )

    original_subset = [
        q
        for q in source_subset
        if mode in question_allowed_modes(q)
    ]

    subjects = sorted(
        {
            q['subject']
            for q in original_subset
        }
    )

    subject = None
    practice_content = None
    practice_pool = list(
        original_subset
    )

    with c2:
        if mode == 'Práctica':
            if reinforcement_bank:
                practice_options = [
                    (
                        'Original + refuerzo piloto '
                        f'({len(reinforcement_bank)})'
                    ),
                    'Solo originales',
                    (
                        'Solo refuerzo piloto '
                        f'({len(reinforcement_bank)})'
                    ),
                ]
            else:
                practice_options = [
                    'Solo originales'
                ]

                st.warning(
                    'No hay preguntas de refuerzo '
                    'validadas disponibles.'
                )

            practice_content = st.selectbox(
                'Contenido de práctica',
                practice_options,
            )

            if practice_content.startswith(
                'Original + refuerzo'
            ):
                practice_pool = (
                    list(
                        original_subset
                    )
                    + list(
                        reinforcement_bank
                    )
                )

            elif practice_content.startswith(
                'Solo refuerzo'
            ):
                practice_pool = list(
                    reinforcement_bank
                )

            else:
                practice_pool = list(
                    original_subset
                )

            max_amount = min(
                60,
                len(practice_pool),
            )

            if max_amount < 1:
                st.error(
                    'No hay preguntas disponibles '
                    'para esta práctica.'
                )

                amount = 0

            else:
                min_amount = (
                    1
                    if max_amount < 5
                    else 5
                )

                default_amount = min(
                    30,
                    max_amount,
                )

                default_amount = max(
                    min_amount,
                    default_amount,
                )

                amount = st.slider(
                    'Preguntas',
                    min_amount,
                    max_amount,
                    default_amount,
                )

        elif mode == 'Por área':
            subject = st.selectbox(
                'Área',
                subjects,
            )

            available = len(
                [
                    q
                    for q in original_subset
                    if q['subject'] == subject
                ]
            )

            amount = st.slider(
                'Preguntas',
                5,
                min(
                    60,
                    available,
                ),
                min(
                    20,
                    available,
                ),
            )

        elif mode == 'Examen rápido':
            amount = st.slider(
                'Preguntas',
                10,
                min(
                    60,
                    len(
                        original_subset
                    ),
                ),
                min(
                    30,
                    len(
                        original_subset
                    ),
                ),
                5,
            )

        else:
            amount = 90
            subject = None

        # P2-E2B ADAPTIVE UI
        adaptive_available = (
            mode in (
                'Práctica',
                'Examen rápido',
                'Por área',
            )
            and not (
                mode == 'Práctica'
                and not practice_content.startswith(
                    'Solo originales'
                )
            )
        )

        adaptive_selection = st.checkbox(
            'Selección inteligente',
            value=False,
            disabled=not adaptive_available,
            help=(
                'Usa el mapa curricular UTN, prioriza exposición '
                'equilibrada y, cuando exista evidencia detallada, '
                'da más práctica a temas menos dominados. Solo usa '
                'preguntas C0-C2 aptas para lápiz y papel.'
            ),
        )

        if (
            mode == 'Práctica'
            and not adaptive_available
        ):
            st.caption(
                'La selección inteligente se habilita en Práctica '
                'con Solo originales. Original + refuerzo y Solo '
                'refuerzo conservan su selección normal.'
            )

        shuffle = st.checkbox(
            'Mezclar orden',
            value=(
                mode
                != 'Examen completo'
            ),
        )

    with c3:
        if mode == 'Examen completo':
            minutes = st.number_input(
                'Tiempo (min)',
                30,
                180,
                90,
                5,
            )

        elif mode == 'Práctica':
            minutes = 0

            st.info(
                'En Práctica puedes usar material '
                'de refuerzo validado. Las preguntas '
                'R-xxx son material generado y no '
                'preguntas oficiales UTN.'
            )

        else:
            minutes = st.number_input(
                'Tiempo (min)',
                0,
                180,
                max(
                    20,
                    int(amount),
                ),
                5,
            )

        st.info(
            'Las preguntas marcadas **REVISAR FUENTE** '
            'contienen una inconsistencia o ambigüedad '
            'detectada en el PDF original.'
        )

    # --------------------------------------------------------
    # INICIO
    # --------------------------------------------------------

    if st.button(
        '▶ Iniciar',
        type='primary',
        use_container_width=True,
        disabled=(
            mode == 'Práctica'
            and not practice_pool
        ),
    ):
        # ----------------------------------------------------
        # BARRERA CENTRAL P2-D5:
        #
        # Práctica puede usar reinforcement_bank.
        # Cualquier otro modo usa SOLO bank original.
        # ----------------------------------------------------

        if mode == 'Práctica':
            qs = list(
                practice_pool
            )
        else:
            qs = list(
                original_subset
            )

        if subject:
            qs = [
                q
                for q in qs
                if q['subject'] == subject
            ]

        adaptive_meta = None

        if (
            mode == 'Examen completo'
            and not source.startswith(
                'Mixto'
            )
        ):
            qs = sorted(
                qs,
                key=lambda x: x['number'],
            )[:90]

        else:
            if adaptive_selection:
                adaptive_context = load_adaptive_context(
                    BASE
                )

                adaptive_candidates = [
                    q
                    for q in qs
                    if str(
                        q.get(
                            'id',
                            ''
                        )
                    )
                    in adaptive_context.metadata
                ]

                if len(adaptive_candidates) < amount:
                    st.error(
                        'La selección inteligente tiene '
                        f'{len(adaptive_candidates)} preguntas '
                        'C0-C2 elegibles en este conjunto, '
                        f'pero solicitaste {amount}. '
                        'Reduce la cantidad o usa selección normal.'
                    )
                    return

                qs = select_adaptive_questions(
                    candidates=qs,
                    amount=amount,
                    history=load_history(),
                    mode=mode,
                    context=adaptive_context,
                )

                if len(qs) != amount:
                    st.error(
                        'La selección inteligente no pudo '
                        'completar la cantidad solicitada.'
                    )
                    return

                adaptive_meta = {
                    'selector_version':
                        'p2e2_curriculum_c0c2_v1',
                    'requested': int(
                        amount
                    ),
                    'selected': len(
                        qs
                    ),
                    'eligible_candidates': len(
                        adaptive_candidates
                    ),
                    'cold_start': not any(
                        isinstance(
                            record,
                            dict
                        )
                        and record.get(
                            'schema_version'
                        )
                        == 'p2e1_attempt_v1'
                        for record in load_history()
                    ),
                }

            elif len(qs) > amount:
                qs = random.sample(
                    qs,
                    amount,
                )

            if not shuffle:
                qs = sorted(
                    qs,
                    key=lambda x: (
                        str(
                            x.get(
                                'simulacro',
                                ''
                            )
                        ),
                        str(
                            x.get(
                                'number',
                                x.get(
                                    'id',
                                    ''
                                )
                            )
                        ),
                    ),
                )

        if shuffle:
            random.shuffle(
                qs
            )

        st.session_state.exam_questions = qs
        st.session_state.selection_strategy = (
            'adaptive_curriculum_c0c2_v1'
            if adaptive_selection
            else 'standard_v1'
        )
        st.session_state.adaptive_selection_meta = (
            adaptive_meta
        )
        st.session_state.exam_idx = 0
        st.session_state.answers = {}
        st.session_state.flags = set()
        st.session_state.start_ts = time.time()
        st.session_state.duration_min = int(
            minutes
        )
        st.session_state.submitted = False

        if mode == 'Práctica':
            st.session_state.exam_title = (
                f'{mode} - {practice_content}'
            )
        else:
            st.session_state.exam_title = (
                f'{mode} - {source}'
                + (
                    f' - {subject}'
                    if subject
                    else ''
                )
            )

        st.session_state.mode = mode
        analytics_start_attempt(
            st,
            mode=mode,
            title=st.session_state.exam_title,
            questions=qs,
        )

        st.rerun()


def timer_block():
    mins = st.session_state.get('duration_min', 0)
    if not mins:
        st.metric('Tiempo', 'Sin límite')
        return False
    elapsed = time.time() - st.session_state.start_ts
    remaining = max(0, mins*60 - elapsed)
    mm, ss = divmod(int(remaining), 60)
    st.metric('Tiempo restante', f'{mm:02d}:{ss:02d}')
    if remaining <= 0:
        st.session_state.submitted = True
        return True
    return False


def question_navigator(qs):
    st.subheader('Navegación')
    answered = st.session_state.answers
    flags = st.session_state.flags
    cols = st.columns(5)
    for i,q in enumerate(qs):
        label = f"{i+1}"
        help_txt = f"{q['id']} - {q['subject']}"
        with cols[i % 5]:
            prefix = '✅ ' if q['id'] in answered else ('🚩 ' if q['id'] in flags else '')
            if st.button(prefix+label, key=f'nav_{i}', help=help_txt, use_container_width=True):
                st.session_state.exam_idx = i
                st.rerun()



def exam_screen(bank):
    qs = st.session_state.exam_questions
    idx = st.session_state.exam_idx
    q = qs[idx]
    analytics_question_view(st, q, idx + 1)

    top1, top2, top3, top4 = st.columns(
        [2.2, 1, 1, 1]
    )

    with top1:
        st.subheader(
            st.session_state.exam_title
        )

    with top2:
        st.metric(
            'Pregunta',
            f'{idx+1}/{len(qs)}',
        )

    with top3:
        st.metric(
            'Respondidas',
            (
                f"{len(st.session_state.answers)}"
                f"/{len(qs)}"
            ),
        )

    with top4:
        expired = timer_block()

    if expired:
        st.warning(
            'El tiempo terminó. '
            'Se cerró el intento automáticamente.'
        )

        st.session_state.submitted = True
        st.rerun()

    reinforcement = is_reinforcement_question(
        q
    )

    left, right = st.columns(
        [2.25, 1],
        gap='large',
    )

    # ========================================================
    # CONTENIDO
    # ========================================================

    with left:
        if reinforcement:
            st.markdown(
                f"### Refuerzo FICA · {q.get('subject', 'Área')}"
            )

            st.markdown(
                '<div class="badge-ok">'
                '<b>✓ REFUERZO VALIDADO</b> · '
                'material generado para preparación FICA. '
                'No es una pregunta oficial UTN.'
                '</div>',
                unsafe_allow_html=True,
            )

            unit = q.get(
                'unit'
            )

            cluster = q.get(
                'cluster'
            )

            metadata = [
                value
                for value in [
                    unit,
                    cluster,
                ]
                if value
            ]

            if metadata:
                st.caption(
                    ' · '.join(
                        str(value)
                        for value in metadata
                    )
                )

            st.markdown(
                '#### Enunciado'
            )

            st.write(
                q.get(
                    'stem',
                    'Enunciado no disponible.'
                )
            )

            st.caption(
                'Esta pregunta es autocontenida y '
                'no depende de los PDF de los simulacros.'
            )

        else:
            st.markdown(
                f"### {q['simulacro']} · "
                f"Pregunta {q['number']} · "
                f"{q['subject']}"
            )

            if q['status'] == 'revisar_fuente':
                st.markdown(
                    '<div class="badge-warn">'
                    '<b>⚠ REVISAR FUENTE:</b> '
                    'el enunciado/opciones presentan '
                    'una inconsistencia detectada.'
                    '</div>',
                    unsafe_allow_html=True,
                )

            else:
                st.markdown(
                    '<div class="badge-ok">'
                    '✓ Clave resuelta por '
                    'razonamiento independiente.'
                    '</div>',
                    unsafe_allow_html=True,
                )

            st.image(
                render_pdf_page(
                    q['pdf'],
                    q['page'],
                ),
                caption=(
                    f"Página original {q['page']} - "
                    f"localice la pregunta {q['number']}"
                ),
                use_container_width=True,
            )

            st.caption(
                'Se conserva la página original completa '
                'porque algunas preguntas contienen '
                'diagramas, matrices, fórmulas o tablas '
                'que se degradan al extraer solo texto.'
            )

    # ========================================================
    # RESPUESTA
    # ========================================================

    with right:
        current = st.session_state.answers.get(
            q['id']
        )

        options, labels = question_choice_data(
            q
        )

        if not options:
            st.error(
                'La pregunta no contiene '
                'alternativas válidas.'
            )

            ans = None

        else:
            index = (
                options.index(
                    current
                )
                if current in options
                else None
            )

            ans = st.radio(
                'Seleccione una opción',
                options,
                index=index,
                horizontal=(
                    not reinforcement
                ),
                format_func=lambda key: (
                    choice_display_text(
                        key,
                        labels,
                    )
                ),
                key=(
                    f"radio_{q['id']}_{idx}"
                ),
            )

        if ans:
            st.session_state.answers[
                q['id']
            ] = ans
            analytics_answer_selected(st, q, ans)

        flag = (
            q['id']
            in st.session_state.flags
        )

        if st.checkbox(
            '🚩 Marcar para revisar',
            value=flag,
            key=f'flag_{q["id"]}',
        ):
            st.session_state.flags.add(
                q['id']
            )
        else:
            st.session_state.flags.discard(
                q['id']
            )
        analytics_set_flag(
            st,
            q,
            q['id'] in st.session_state.flags,
        )

        # ----------------------------------------------------
        # FEEDBACK:
        # únicamente en Práctica.
        # ----------------------------------------------------

        practice = (
            st.session_state.mode
            == 'Práctica'
        )

        if practice and ans:
            if canonical_answer_key(ans) == canonical_answer_key(q.get('answer')):
                st.success(
                    f"Correcto: {q['answer']}"
                )
            else:
                st.error(
                    f"Tu respuesta: {ans} · "
                    f"Clave propuesta: {q['answer']}"
                )

            if reinforcement:
                render_reinforcement_explanation(
                    q
                )
            else:
                render_original_explanation(
                    q
                )

        st.divider()

        b1, b2 = st.columns(2)

        with b1:
            if st.button(
                '← Anterior',
                disabled=(
                    idx == 0
                ),
                use_container_width=True,
            ):
                st.session_state.exam_idx -= 1
                st.rerun()

        with b2:
            if st.button(
                'Siguiente →',
                disabled=(
                    idx
                    == len(qs) - 1
                ),
                use_container_width=True,
            ):
                st.session_state.exam_idx += 1
                st.rerun()

        if idx == len(qs) - 1:
            if st.button(
                '🏁 Finalizar y corregir',
                type='primary',
                use_container_width=True,
            ):
                st.session_state.submitted = True
                st.rerun()

        if st.button(
            'Salir sin guardar',
            use_container_width=True,
        ):
            reset_exam()
            st.rerun()

    with st.sidebar:
        question_navigator(
            qs
        )

        st.divider()

        st.caption(
            'Leyenda: ✅ respondida · 🚩 revisar'
        )



def results_screen():
    qs = st.session_state.exam_questions
    answers = st.session_state.answers

    rows = []

    for q in qs:
        user = answers.get(
            q['id'],
            '—',
        )

        correct_answer = (
            canonical_answer_key(user) == canonical_answer_key(q.get('answer'))
        )

        rows.append(
            {
                'ID': q['id'],
                'Simulacro': q.get(
                    'simulacro',
                    'Refuerzo FICA',
                ),
                'Nº': q.get(
                    'number',
                    q['id'],
                ),
                'Área': q.get(
                    'subject',
                    q.get(
                        'area',
                        'Sin área',
                    ),
                ),
                'Tu respuesta': user,
                'Clave': q['answer'],
                'Correcta': correct_answer,
                'Estado fuente': q.get(
                    'status',
                    'refuerzo_validado',
                ),
            }
        )

    df = pd.DataFrame(
        rows
    )

    correct = int(
        df['Correcta'].sum()
    )

    total = len(
        df
    )

    pct = (
        round(
            correct
            / total
            * 100,
            1,
        )
        if total
        else 0
    )

    unanswered = sum(
        1
        for row in rows
        if row['Tu respuesta'] == '—'
    )

    if 'final_elapsed' not in st.session_state:
        st.session_state.final_elapsed = int(
            time.time() - st.session_state.start_ts
        )
    elapsed = int(st.session_state.final_elapsed)

    st.title(
        '📊 Resultado del intento'
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        'Puntaje',
        f'{correct}/{total}',
    )

    c2.metric(
        'Porcentaje',
        f'{pct}%',
    )

    c3.metric(
        'Sin responder',
        unanswered,
    )

    c4.metric(
        'Tiempo usado',
        (
            f'{elapsed//60}m '
            f'{elapsed%60}s'
        ),
    )

    area = (
        df.groupby(
            'Área'
        )[
            'Correcta'
        ]
        .agg(
            [
                'sum',
                'count',
            ]
        )
        .reset_index()
    )

    area[
        'Porcentaje'
    ] = (
        area['sum']
        / area['count']
        * 100
    ).round(1)

    area.columns = [
        'Área',
        'Correctas',
        'Preguntas',
        'Porcentaje',
    ]

    st.subheader(
        'Rendimiento por área'
    )

    st.dataframe(
        area,
        use_container_width=True,
        hide_index=True,
    )

    st.subheader(
        'Revisión pregunta por pregunta'
    )

    only_wrong = st.checkbox(
        'Mostrar solo incorrectas o no respondidas',
        value=True,
    )

    show_rows = (
        rows
        if not only_wrong
        else [
            row
            for row in rows
            if not row['Correcta']
        ]
    )

    qmap = {
        q['id']: q
        for q in qs
    }

    for row in show_rows:
        q = qmap[
            row['ID']
        ]

        icon = (
            '✅'
            if row['Correcta']
            else '❌'
        )

        with st.expander(
            f"{icon} {q['id']} · "
            f"{q.get('subject', q.get('area', 'Área'))} · "
            f"tu respuesta {row['Tu respuesta']} / "
            f"clave {q['answer']}"
        ):
            if is_reinforcement_question(
                q
            ):
                st.info(
                    'Material de refuerzo generado '
                    'y validado. No es una pregunta '
                    'oficial UTN.'
                )

                st.markdown(
                    '**Enunciado**'
                )

                st.write(
                    q.get(
                        'stem',
                        'Enunciado no disponible.',
                    )
                )

                options, labels = (
                    question_choice_data(
                        q
                    )
                )

                if options:
                    st.markdown(
                        '**Alternativas**'
                    )

                    for key in options:
                        marker = (
                            '✓'
                            if key == q['answer']
                            else '•'
                        )

                        st.write(
                            f"{marker} "
                            f"{choice_display_text(key, labels)}"
                        )

                render_reinforcement_explanation(
                    q
                )

            else:
                if (
                    q['status']
                    == 'revisar_fuente'
                ):
                    st.warning(
                        q['source_note']
                    )

                st.write(
                    q['explanation']
                )

                st.image(
                    render_pdf_page(
                        q['pdf'],
                        q['page'],
                        1.15,
                    ),
                    caption=(
                        f"Fuente: página {q['page']}, "
                        f"pregunta {q['number']}"
                    ),
                    use_container_width=True,
                )

    # P2-E1 DETAILED MASTERY EVIDENCE
    history_items = []

    for q in qs:
        user = answers.get(
            q['id'],
            '—',
        )

        quality = q.get(
            'quality',
            {},
        )

        mastery_eligible = (
            isinstance(
                quality,
                dict,
            )
            and quality.get(
                'mastery_eligible'
            )
            is True
            and quality.get(
                'status'
            )
            in (
                None,
                'validated',
            )
            and q.get(
                'status'
            )
            != 'revisar_fuente'
            and q.get(
                'taxonomy_status'
            )
            != 'source_review'
            and not is_reinforcement_question(
                q
            )
        )

        history_items.append(
            {
                'question_id': q['id'],
                'answered': user != '—',
                'correct': canonical_answer_key(user) == canonical_answer_key(q.get('answer')),
                'user_answer': (
                    None
                    if user == '—'
                    else user
                ),
                'correct_answer': q['answer'],
                'simulacro': q.get(
                    'simulacro',
                    'Refuerzo FICA',
                ),
                'area': q.get(
                    'subject',
                    q.get(
                        'area',
                        'Sin área',
                    ),
                ),
                'topic': q.get(
                    'topic',
                    'Sin tema',
                ),
                'skill': q.get(
                    'skill',
                    'Sin habilidad',
                ),
                'subskill': q.get(
                    'subskill',
                    'Sin subhabilidad',
                ),
                'content_origin': q.get(
                    'content_origin',
                    q.get(
                        'origin',
                        'legacy',
                    ),
                ),
                'content_package_id': q.get(
                    'content_package_id',
                ),
                'taxonomy_status': q.get(
                    'taxonomy_status',
                ),
                'mastery_eligible': mastery_eligible,
            }
        )

    if not st.session_state.get(
        'last_saved_attempt'
    ):
        analytics_complete_attempt(
            st,
            qs=qs,
            answers=answers,
            correct=correct,
            total=total,
            pct=pct,
            detailed_items=locals().get('history_items'),
        )
        save_history(
            {
                'schema_version': 'p2e1_attempt_v1',
                'fecha': datetime.now().isoformat(
                    timespec='seconds'
                ),
                'titulo': st.session_state.exam_title,
                'mode': st.session_state.get(
                    'mode',
                    'Desconocido',
                ),
                'selection_strategy': st.session_state.get(
                    'selection_strategy',
                    'standard_v1',
                ),
                'correctas': correct,
                'total': total,
                'porcentaje': pct,
                'tiempo_segundos': elapsed,
                'areas': area.to_dict(
                    orient='records'
                ),
                'items': history_items,
            }
        )

        st.session_state.last_saved_attempt = True

    st.download_button(
        'Descargar resultado CSV',
        data=df.to_csv(
            index=False
        ).encode(
            'utf-8-sig'
        ),
        file_name=(
            'resultado_utn_'
            f"{datetime.now().strftime('%Y%m%d_%H%M')}"
            '.csv'
        ),
        mime='text/csv',
    )

    if st.button(
        '↻ Nuevo intento',
        type='primary',
    ):
        reset_exam()
        st.rerun()


def dashboard_screen(
    runtime_bank,
):
    try:
        model = build_dashboard_model(
            history=load_history(),
            runtime_bank=runtime_bank,
            project_root=Path(__file__).resolve().parent,
        )
    except Exception as exc:
        st.error(
            "No se pudo construir el dashboard: "
            f"{exc}"
        )
        return

    render_dashboard(
        st,
        pd,
        model,
    )


def history_screen():
    render_history_screen(
        st=st,
        pd=pd,
        history=load_history(),
        history_path=HISTORY,
    )


def recommendations_screen():
    history = load_history()

    persistent_state = sync_persistent_state(
        history=history,
        project_root=Path(_mobile_ctx["user_root"]),
    )

    # P2-MOBILE: recommendations state remote sync.
    mobile_push_user_files(
        st,
        _mobile_ctx,
    )

    model = persistent_state[
        'recommendations_model'
    ]

    render_recommendations(
        st=st,
        pd=pd,
        model=model,
    )

    sync_status = persistent_state.get(
        '_sync_status',
        'reused',
    )

    st.caption(
        'Estado persistente local: '
        + (
            'actualizado'
            if sync_status == 'rebuilt'
            else 'sincronizado'
        )
    )



# P2-MOBILE RC1 - login + cache privado por estudiante
_mobile_ctx = mobile_bootstrap(
    st,
    BASE,
)

if _mobile_ctx is None:
    st.stop()

# En P2 Mobile, HISTORY es privado por estudiante/sesion y se sincroniza
# con el backend remoto. El P2 original permanece intacto.
HISTORY = Path(
    _mobile_ctx["history_path"]
)

# P2-M5A.4 - ANALYTICS INSTRUMENTATION
_p2_analytics_engine = ensure_analytics(
    st,
    _mobile_ctx,
    app_version="P2-MOBILE-M5A.4",
)

bank = load_bank()
reinforcement_bank = load_reinforcement_bank()

# P2-E7 - estado persistente derivado.
# history.json continua siendo la fuente de verdad.
_p2e7_state = sync_persistent_state(
    history=load_history(),
    project_root=Path(_mobile_ctx["user_root"]),
)

# P2-MOBILE: sync derived state to backend.
mobile_push_user_files(
    st,
    _mobile_ctx,
)

with st.sidebar:
    st.title(
        'UTN · Entrenamiento'
    )

    mobile_sidebar_account(
        st,
        _mobile_ctx,
        on_logout=lambda: analytics_logout(st),
    )

    st.divider()

    page = st.radio(
        'Sección',
        [
            'Simulador',
            'Dashboard',
            'Historial',
            'Recomendaciones',
        ],
        index=0,
    )

    st.caption(
        f'Banco original: {len(bank)} preguntas'
    )

    st.caption(
        'Refuerzo validado: '
        f'{len(reinforcement_bank)} preguntas'
    )

    if reinforcement_bank:
        st.caption(
            'Las preguntas R-xxx aparecen '
            'únicamente en Práctica.'
        )
analytics_page_view(st, page)

if (
    page == 'Dashboard'
    and 'exam_questions'
    not in st.session_state
):
    dashboard_screen(
        bank,
    )

elif (
    page == 'Historial'
    and 'exam_questions'
    not in st.session_state
):
    history_screen()


elif (
    page == 'Recomendaciones'
    and 'exam_questions'
    not in st.session_state
):
    recommendations_screen()

elif (
    'exam_questions'
    not in st.session_state
):
    configure_exam(
        bank,
        reinforcement_bank,
    )

elif st.session_state.get(
    'submitted'
):
    results_screen()

else:
    exam_screen(
        bank
    )
