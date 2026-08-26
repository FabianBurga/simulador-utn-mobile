from __future__ import annotations
import json
import random
import time
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / 'data' / 'question_bank.json'
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

@st.cache_data
def load_bank():
    return json.loads(DATA_FILE.read_text(encoding='utf-8'))

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


def reset_exam():
    for k in ['exam_questions','exam_idx','answers','flags','start_ts','duration_min','submitted','exam_title','mode','last_saved_attempt']:
        st.session_state.pop(k, None)


def configure_exam(bank):
    st.title('🎓 Simulador UTN Interactivo - FICA/FICAYA')
    st.caption('Práctica local basada en los dos simulacros adjuntos. Las páginas originales se muestran para conservar fórmulas, diagramas y tablas.')

    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    with c1:
        source = st.selectbox('Banco', ['Simulacro 1', 'Simulacro 3', 'Mixto (ambos)'])
        mode = st.selectbox('Modalidad', ['Examen completo', 'Práctica', 'Examen rápido', 'Por área'])
    subset = bank if source.startswith('Mixto') else [q for q in bank if q['simulacro'] == source]
    subjects = sorted({q['subject'] for q in subset})
    with c2:
        if mode == 'Por área':
            subject = st.selectbox('Área', subjects)
            amount = st.slider('Preguntas', 5, min(60, len([q for q in subset if q['subject']==subject])), min(20, len([q for q in subset if q['subject']==subject])))
        elif mode == 'Examen rápido':
            amount = st.slider('Preguntas', 10, min(60, len(subset)), min(30, len(subset)), 5)
            subject = None
        else:
            amount = 90 if mode == 'Examen completo' else min(30, len(subset))
            subject = None
        shuffle = st.checkbox('Mezclar orden', value=mode != 'Examen completo')
    with c3:
        if mode == 'Examen completo':
            minutes = st.number_input('Tiempo (min)', 30, 180, 90, 5)
        elif mode == 'Práctica':
            minutes = 0
        else:
            minutes = st.number_input('Tiempo (min)', 0, 180, max(20, int(amount)), 5)
        st.info('Las preguntas marcadas **REVISAR FUENTE** contienen una inconsistencia o ambigüedad detectada en el PDF original.')

    if st.button('▶ Iniciar', type='primary', use_container_width=True):
        qs = subset
        if subject:
            qs = [q for q in qs if q['subject'] == subject]
        if mode == 'Examen completo' and not source.startswith('Mixto'):
            qs = sorted(qs, key=lambda x:x['number'])[:90]
        else:
            if len(qs) > amount:
                qs = random.sample(qs, amount)
            if not shuffle:
                qs = sorted(qs, key=lambda x:(x['simulacro'], x['number']))
        if shuffle:
            random.shuffle(qs)
        st.session_state.exam_questions = qs
        st.session_state.exam_idx = 0
        st.session_state.answers = {}
        st.session_state.flags = set()
        st.session_state.start_ts = time.time()
        st.session_state.duration_min = int(minutes)
        st.session_state.submitted = False
        st.session_state.exam_title = f'{mode} - {source}' + (f' - {subject}' if subject else '')
        st.session_state.mode = mode
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

    top1, top2, top3, top4 = st.columns([2.2, 1, 1, 1])
    with top1:
        st.subheader(st.session_state.exam_title)
    with top2:
        st.metric('Pregunta', f'{idx+1}/{len(qs)}')
    with top3:
        st.metric('Respondidas', f"{len(st.session_state.answers)}/{len(qs)}")
    with top4:
        expired = timer_block()

    if expired:
        st.warning('El tiempo terminó. Se cerró el intento automáticamente.')
        st.session_state.submitted = True
        st.rerun()

    left, right = st.columns([2.25, 1], gap='large')
    with left:
        st.markdown(f"### {q['simulacro']} · Pregunta {q['number']} · {q['subject']}")
        if q['status'] == 'revisar_fuente':
            st.markdown('<div class="badge-warn"><b>⚠ REVISAR FUENTE:</b> el enunciado/opciones presentan una inconsistencia detectada.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="badge-ok">✓ Clave resuelta por razonamiento independiente.</div>', unsafe_allow_html=True)
        st.image(render_pdf_page(q['pdf'], q['page']), caption=f"Página original {q['page']} - localice la pregunta {q['number']}", use_container_width=True)
        st.caption('Se conserva la página original completa porque algunas preguntas contienen diagramas, matrices, fórmulas o tablas que se degradan al extraer solo texto.')

    with right:
        current = st.session_state.answers.get(q['id'])
        options = q['choices']
        index = options.index(current) if current in options else None
        ans = st.radio('Seleccione una opción', options, index=index, horizontal=True, key=f"radio_{q['id']}_{idx}")
        if ans:
            st.session_state.answers[q['id']] = ans
        flag = q['id'] in st.session_state.flags
        if st.checkbox('🚩 Marcar para revisar', value=flag, key=f'flag_{q["id"]}'):
            st.session_state.flags.add(q['id'])
        else:
            st.session_state.flags.discard(q['id'])

        practice = st.session_state.mode == 'Práctica'
        if practice and ans:
            if ans == q['answer']:
                st.success(f"Correcto: {q['answer']}")
            else:
                st.error(f"Tu respuesta: {ans} · Clave propuesta: {q['answer']}")
            st.markdown('#### Cómo resolverlo')
            st.write(q['explanation'])
            if q.get('source_note'):
                st.warning(q['source_note'])

        st.divider()
        b1,b2 = st.columns(2)
        with b1:
            if st.button('← Anterior', disabled=idx==0, use_container_width=True):
                st.session_state.exam_idx -= 1; st.rerun()
        with b2:
            if st.button('Siguiente →', disabled=idx==len(qs)-1, use_container_width=True):
                st.session_state.exam_idx += 1; st.rerun()
        if idx == len(qs)-1:
            if st.button('🏁 Finalizar y corregir', type='primary', use_container_width=True):
                st.session_state.submitted = True; st.rerun()
        if st.button('Salir sin guardar', use_container_width=True):
            reset_exam(); st.rerun()

    with st.sidebar:
        question_navigator(qs)
        st.divider()
        st.caption('Leyenda: ✅ respondida · 🚩 revisar')


def results_screen():
    qs = st.session_state.exam_questions
    answers = st.session_state.answers
    rows=[]
    for q in qs:
        user = answers.get(q['id'],'—')
        ok = user == q['answer']
        rows.append({'ID':q['id'],'Simulacro':q['simulacro'],'Nº':q['number'],'Área':q['subject'],'Tu respuesta':user,'Clave':q['answer'],'Correcta':ok,'Estado fuente':q['status']})
    df=pd.DataFrame(rows)
    correct=int(df['Correcta'].sum())
    total=len(df)
    pct=round(correct/total*100,1) if total else 0
    unanswered=sum(1 for x in rows if x['Tu respuesta']=='—')
    elapsed=int(time.time()-st.session_state.start_ts)

    st.title('📊 Resultado del intento')
    c1,c2,c3,c4=st.columns(4)
    c1.metric('Puntaje',f'{correct}/{total}')
    c2.metric('Porcentaje',f'{pct}%')
    c3.metric('Sin responder',unanswered)
    c4.metric('Tiempo usado',f'{elapsed//60}m {elapsed%60}s')

    area=(df.groupby('Área')['Correcta'].agg(['sum','count']).reset_index())
    area['Porcentaje']=(area['sum']/area['count']*100).round(1)
    area.columns=['Área','Correctas','Preguntas','Porcentaje']
    st.subheader('Rendimiento por área')
    st.dataframe(area, use_container_width=True, hide_index=True)

    st.subheader('Revisión pregunta por pregunta')
    only_wrong=st.checkbox('Mostrar solo incorrectas o no respondidas', value=True)
    show_rows=rows if not only_wrong else [r for r in rows if not r['Correcta']]
    qmap={q['id']:q for q in qs}
    for r in show_rows:
        q=qmap[r['ID']]
        icon='✅' if r['Correcta'] else '❌'
        with st.expander(f"{icon} {q['id']} · {q['subject']} · tu respuesta {r['Tu respuesta']} / clave {q['answer']}"):
            if q['status']=='revisar_fuente':
                st.warning(q['source_note'])
            st.write(q['explanation'])
            st.image(render_pdf_page(q['pdf'],q['page'],1.15), caption=f"Fuente: página {q['page']}, pregunta {q['number']}", use_container_width=True)

    if not st.session_state.get('last_saved_attempt'):
        save_history({'fecha':datetime.now().isoformat(timespec='seconds'),'titulo':st.session_state.exam_title,'correctas':correct,'total':total,'porcentaje':pct,'tiempo_segundos':elapsed,'areas':area.to_dict(orient='records')})
        st.session_state.last_saved_attempt=True

    st.download_button('Descargar resultado CSV', data=df.to_csv(index=False).encode('utf-8-sig'), file_name=f"resultado_utn_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", mime='text/csv')
    if st.button('↻ Nuevo intento', type='primary'):
        reset_exam(); st.rerun()


def history_screen():
    st.title('📈 Historial local')
    hist=load_history()
    if not hist:
        st.info('Aún no hay intentos guardados.')
        return
    df=pd.DataFrame([{k:v for k,v in h.items() if k!='areas'} for h in hist])
    st.dataframe(df.iloc[::-1], use_container_width=True, hide_index=True)
    if 'porcentaje' in df.columns:
        st.line_chart(df.set_index('fecha')['porcentaje'])
    if st.button('Borrar historial local'):
        HISTORY.write_text('[]',encoding='utf-8'); st.rerun()

bank=load_bank()

with st.sidebar:
    st.title('UTN · Entrenamiento')
    page=st.radio('Sección',['Simulador','Historial'],index=0)
    st.caption(f'Banco cargado: {len(bank)} preguntas (2 × 90)')

if page=='Historial' and 'exam_questions' not in st.session_state:
    history_screen()
elif 'exam_questions' not in st.session_state:
    configure_exam(bank)
elif st.session_state.get('submitted'):
    results_screen()
else:
    exam_screen(bank)
