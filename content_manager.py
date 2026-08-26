\
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_SCHEMA = "p2e0_simulacro_manifest_v1"
CATALOG_SCHEMA = "p2e0_content_catalog_v1"
REGISTRY_SCHEMA = "p2e0_content_id_registry_v1"
RUNTIME_SCHEMA = "p2e0_runtime_question_bank_v1"
VALIDATION_FLAGS = (
    "structure_reviewed", "source_reviewed", "answers_reviewed",
    "taxonomy_reviewed", "pedagogical_reviewed", "mastery_approved",
)
CONTENT_MODE_ORDER = (
    "Examen completo",
    "Práctica",
    "Examen rápido",
    "Por área",
)
ALLOWED_MODES_BY_CONTENT_TYPE = {
    "full_simulacro": CONTENT_MODE_ORDER,
    "practice_collection": (
        "Práctica",
        "Examen rápido",
        "Por área",
    ),
}
CONTENT_TYPES = frozenset(ALLOWED_MODES_BY_CONTENT_TYPE)
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")

        os.replace(
            tmp,
            path,
        )

    except Exception:
        tmp.unlink(
            missing_ok=True
        )
        raise


def _paths(root=None):
    root = (
        Path(root)
        if root is not None
        else ROOT
    )

    return {
        "root":
            root,

        "data":
            root / "data",

        "sources":
            root / "sources",

        "packages":
            root / "content" / "simulacros",

        "runtime":
            root / "data" / "runtime_banks",

        "catalog":
            root / "data" / "content_catalog.json",

        "registry":
            root / "data" / "content_id_registry.json",
    }


def _registry(path, core):
    ids = sorted(
        str(
            q.get(
                "id",
                "",
            )
        ).strip()
        for q in core
    )

    if (
        any(
            not x
            for x in ids
        )
        or
        len(ids)
        != len(
            set(ids)
        )
    ):
        raise RuntimeError(
            "IDs inválidos o duplicados en question_bank.json"
        )

    if not Path(
        path
    ).exists():
        return {
            "schema_version":
                REGISTRY_SCHEMA,

            "created_at":
                _now(),

            "updated_at":
                _now(),

            "legacy_ids":
                ids,

            "packages":
                {},
        }

    try:
        data = _read(
            path
        )

    except Exception as exc:
        raise RuntimeError(
            f"Registro de IDs ilegible: {exc}"
        ) from exc

    if (
        not isinstance(
            data,
            dict,
        )
        or
        data.get(
            "schema_version"
        )
        != REGISTRY_SCHEMA
        or
        not isinstance(
            data.get(
                "legacy_ids"
            ),
            list,
        )
        or
        not isinstance(
            data.get(
                "packages"
            ),
            dict,
        )
    ):
        raise RuntimeError(
            "Schema inválido en content_id_registry.json"
        )

    if (
        set(
            data[
                "legacy_ids"
            ]
        )
        !=
        set(
            ids
        )
    ):
        raise RuntimeError(
            "Los IDs legado no coinciden con el registro estable"
        )

    return data


def _name(value, field):
    if (
        not isinstance(
            value,
            str,
        )
        or
        not value.strip()
    ):
        raise ValueError(
            f"{field}_missing"
        )

    value = value.strip()

    if (
        Path(
            value
        ).name
        != value
        or
        value
        in {
            ".",
            "..",
        }
    ):
        raise ValueError(
            f"{field}_invalid"
        )

    return value


def _manifest_errors(m, folder):
    if not isinstance(
        m,
        dict,
    ):
        return [
            "manifest_not_object"
        ]

    e = []

    if (
        m.get(
            "schema_version"
        )
        != MANIFEST_SCHEMA
    ):
        e.append(
            "manifest_schema_invalid"
        )

    pid = str(
        m.get(
            "package_id",
            "",
        )
    ).strip()

    name = str(
        m.get(
            "display_name",
            "",
        )
    ).strip()

    prefix = str(
        m.get(
            "id_prefix",
            "",
        )
    ).strip()

    if not PACKAGE_RE.fullmatch(
        pid
    ):
        e.append(
            "package_id_invalid"
        )

    if not name:
        e.append(
            "display_name_missing"
        )

    elif name.startswith(
        "Mixto"
    ):
        e.append(
            "display_name_reserved"
        )

    if not PREFIX_RE.fullmatch(
        prefix
    ):
        e.append(
            "id_prefix_invalid"
        )

    try:
        pdf = (
            folder
            /
            _name(
                m.get(
                    "source_pdf"
                ),
                "source_pdf",
            )
        )

        raw = (
            pdf.read_bytes()
            if pdf.exists()
            else b""
        )

        if (
            not pdf.exists()
            or
            len(
                raw
            )
            < 8
            or
            not raw.startswith(
                b"%PDF-"
            )
        ):
            e.append(
                "source_pdf_invalid"
            )

    except Exception:
        e.append(
            "source_pdf_invalid"
        )

    try:
        qf = (
            folder
            /
            _name(
                m.get(
                    "questions_file",
                    "questions.json",
                ),
                "questions_file",
            )
        )

        if not qf.exists():
            e.append(
                "questions_file_missing"
            )

    except Exception:
        e.append(
            "questions_file_invalid"
        )

    if (
        not isinstance(
            m.get(
                "expected_question_count"
            ),
            int,
        )
        or
        m.get(
            "expected_question_count",
            0,
        )
        < 1
    ):
        e.append(
            "expected_question_count_invalid"
        )

    if (
        m.get(
            "official_utn_question"
        )
        not in {
            True,
            False,
        }
    ):
        e.append(
            "official_utn_question_must_be_boolean"
        )

    content_type = str(
        m.get(
            "content_type",
            "",
        )
    ).strip()

    allowed_modes = m.get(
        "allowed_modes"
    )

    if content_type not in CONTENT_TYPES:
        e.append(
            "content_type_invalid"
        )
    else:
        expected_modes = list(
            ALLOWED_MODES_BY_CONTENT_TYPE[
                content_type
            ]
        )

        if allowed_modes != expected_modes:
            e.append(
                "allowed_modes_invalid_for_content_type"
            )

        if (
            content_type
            == "full_simulacro"
            and m.get(
                "expected_question_count"
            )
            != 90
        ):
            e.append(
                "full_simulacro_question_count_must_be_90"
            )

    v = m.get(
        "validation"
    )

    if not isinstance(
        v,
        dict,
    ):
        e.append(
            "validation_block_missing"
        )

    else:
        if (
            v.get(
                "publication_status"
            )
            != "validated"
        ):
            e.append(
                "publication_status_not_validated"
            )

        e.extend(
            f"validation_{flag}_required"
            for flag
            in VALIDATION_FLAGS
            if (
                v.get(
                    flag
                )
                is not True
            )
        )

        if not str(
            v.get(
                "reviewed_by",
                "",
            )
        ).strip():
            e.append(
                "reviewed_by_missing"
            )

        if not str(
            v.get(
                "reviewed_at",
                "",
            )
        ).strip():
            e.append(
                "reviewed_at_missing"
            )

    return e


def _has_answer(
    choices,
    answer,
):
    a = str(
        answer
    ).strip().upper()

    return any(
        str(
            c
        ).strip().upper()
        == a
        or
        str(
            c
        ).strip().upper().startswith(
            a + "."
        )
        or
        str(
            c
        ).strip().upper().startswith(
            a + ")"
        )
        for c
        in choices
    )


def _questions(m, folder):
    raw = _read(
        folder
        /
        _name(
            m.get(
                "questions_file",
                "questions.json",
            ),
            "questions_file",
        )
    )

    if not isinstance(
        raw,
        list,
    ):
        raise ValueError(
            "questions_not_list"
        )

    if (
        len(
            raw
        )
        != m[
            "expected_question_count"
        ]
    ):
        raise ValueError(
            "question_count_mismatch:"
            f"{len(raw)}!="
            f"{m['expected_question_count']}"
        )

    pid = m[
        "package_id"
    ]

    display = m[
        "display_name"
    ]

    prefix = m[
        "id_prefix"
    ]

    pdf = _name(
        m[
            "source_pdf"
        ],
        "source_pdf",
    )

    pattern = re.compile(
        rf"^{re.escape(prefix)}-\d{{2,4}}$"
    )

    ids = set()
    nums = set()
    out = []

    for i, raw_q in enumerate(
        raw,
        1,
    ):
        if not isinstance(
            raw_q,
            dict,
        ):
            raise ValueError(
                f"question_{i}_not_object"
            )

        q = dict(
            raw_q
        )

        qid = str(
            q.get(
                "id",
                "",
            )
        ).strip()

        if not pattern.fullmatch(
            qid
        ):
            raise ValueError(
                f"{qid or i}:id_invalid"
            )

        if qid in ids:
            raise ValueError(
                f"duplicate_id:{qid}"
            )

        ids.add(
            qid
        )

        n = q.get(
            "number"
        )

        if (
            not isinstance(
                n,
                int,
            )
            or
            n < 1
        ):
            raise ValueError(
                f"{qid}:number_invalid"
            )

        if n in nums:
            raise ValueError(
                f"duplicate_number:{n}"
            )

        nums.add(
            n
        )

        if (
            not isinstance(
                q.get(
                    "page"
                ),
                int,
            )
            or
            q[
                "page"
            ]
            < 1
        ):
            raise ValueError(
                f"{qid}:page_invalid"
            )

        if not str(
            q.get(
                "subject",
                "",
            )
        ).strip():
            raise ValueError(
                f"{qid}:subject_missing"
            )

        answer = str(
            q.get(
                "answer",
                "",
            )
        ).strip()

        choices = q.get(
            "choices"
        )

        if not answer:
            raise ValueError(
                f"{qid}:answer_missing"
            )

        if (
            not isinstance(
                choices,
                list,
            )
            or
            len(
                choices
            )
            < 2
        ):
            raise ValueError(
                f"{qid}:choices_invalid"
            )

        if not _has_answer(
            choices,
            answer,
        ):
            raise ValueError(
                f"{qid}:answer_not_in_choices"
            )

        if not str(
            q.get(
                "explanation",
                "",
            )
        ).strip():
            raise ValueError(
                f"{qid}:explanation_missing"
            )

        for field in (
            "area",
            "topic",
            "skill",
            "subskill",
        ):
            if not str(
                q.get(
                    field,
                    "",
                )
            ).strip():
                raise ValueError(
                    f"{qid}:{field}_missing"
                )

        if (
            q.get(
                "taxonomy_status"
            )
            == "source_review"
            or
            q.get(
                "status"
            )
            == "revisar_fuente"
        ):
            raise ValueError(
                f"{qid}:source_review_not_publishable"
            )

        quality = q.get(
            "quality"
        )

        if (
            not isinstance(
                quality,
                dict,
            )
            or
            quality.get(
                "status"
            )
            != "validated"
        ):
            raise ValueError(
                f"{qid}:quality_not_validated"
            )

        if (
            quality.get(
                "mastery_eligible"
            )
            is not True
        ):
            raise ValueError(
                f"{qid}:mastery_not_eligible"
            )

        q.update(
            {
                "id":
                    qid,

                "simulacro":
                    display,

                "answer":
                    answer,

                "pdf":
                    f"imported/{pid}/{pdf}",

                "official_utn_question":
                    m[
                        "official_utn_question"
                    ],

                "content_type":
                    m[
                        "content_type"
                    ],

                "allowed_modes":
                    list(
                        m[
                            "allowed_modes"
                        ]
                    ),

                "content_package_id":
                    pid,

                "content_origin":
                    "extension_package",
            }
        )

        q.setdefault(
            "source_note",
            "",
        )

        out.append(
            q
        )

    return out


def _stable_stage(
    reg,
    m,
    questions,
    seen,
):
    pid = m[
        "package_id"
    ]

    prefix = m[
        "id_prefix"
    ]

    old = reg.get(
        "packages",
        {},
    ).get(
        pid
    )

    if (
        old
        and
        old.get(
            "id_prefix"
        )
        != prefix
    ):
        raise ValueError(
            "stable_id_prefix_violation"
        )

    legacy = set(
        reg[
            "legacy_ids"
        ]
    )

    numbers = {}

    for q in questions:
        qid = q[
            "id"
        ]

        key = str(
            q[
                "number"
            ]
        )

        if qid in legacy:
            raise ValueError(
                f"id_collision_with_legacy:{qid}"
            )

        if qid in seen:
            raise ValueError(
                f"id_collision_between_packages:{qid}"
            )

        if old:
            old_id = old.get(
                "numbers",
                {},
            ).get(
                key
            )

            if (
                old_id
                is not None
                and
                old_id
                != qid
            ):
                raise ValueError(
                    "stable_id_violation:"
                    f"number_{key}:"
                    f"{old_id}->{qid}"
                )

        numbers[
            key
        ] = qid

    return {
        "id_prefix":
            prefix,

        "display_name":
            m[
                "display_name"
            ],

        "numbers":
            numbers,
    }


def refresh_catalog(
    project_root=None,
):
    p = _paths(
        project_root
    )

    for key in (
        "data",
        "sources",
        "packages",
        "runtime",
    ):
        p[
            key
        ].mkdir(
            parents=True,
            exist_ok=True,
        )

    core_file = (
        p[
            "data"
        ]
        /
        "question_bank.json"
    )

    if not core_file.exists():
        raise RuntimeError(
            "Falta data/question_bank.json"
        )

    core = _read(
        core_file
    )

    if not isinstance(
        core,
        list,
    ):
        raise RuntimeError(
            "question_bank.json no es una lista"
        )

    reg = _registry(
        p[
            "registry"
        ],
        core,
    )

    seen = {
        str(
            q[
                "id"
            ]
        ).strip()
        for q
        in core
    }

    published = []
    blocked = []
    staged = {}

    folders = sorted(
        x
        for x
        in p[
            "packages"
        ].iterdir()
        if (
            x.is_dir()
            and
            not x.name.startswith(
                "_"
            )
        )
    )

    for folder in folders:
        entry = {
            "folder":
                folder.name,

            "published":
                False,

            "errors":
                [],
        }

        mf = (
            folder
            /
            "manifest.json"
        )

        if not mf.exists():
            entry[
                "errors"
            ] = [
                "manifest_missing"
            ]

            blocked.append(
                entry
            )

            continue

        try:
            m = _read(
                mf
            )

        except Exception as exc:
            entry[
                "errors"
            ] = [
                f"manifest_unreadable:{exc}"
            ]

            blocked.append(
                entry
            )

            continue

        entry.update(
            {
                "package_id":
                    str(
                        m.get(
                            "package_id",
                            "",
                        )
                    ).strip(),

                "display_name":
                    str(
                        m.get(
                            "display_name",
                            "",
                        )
                    ).strip(),
            }
        )

        errors = _manifest_errors(
            m,
            folder,
        )

        if errors:
            entry[
                "errors"
            ] = errors

            blocked.append(
                entry
            )

            continue

        try:
            qs = _questions(
                m,
                folder,
            )

            stage = _stable_stage(
                reg,
                m,
                qs,
                seen,
            )

        except Exception as exc:
            entry[
                "errors"
            ] = [
                str(
                    exc
                )
            ]

            blocked.append(
                entry
            )

            continue

        bank_path = (
            p[
                "runtime"
            ]
            /
            f"{m['package_id']}.json"
        )

        _write(
            bank_path,
            {
                "schema_version":
                    RUNTIME_SCHEMA,

                "package_id":
                    m[
                        "package_id"
                    ],

                "display_name":
                    m[
                        "display_name"
                    ],

                "content_type":
                    m[
                        "content_type"
                    ],

                "allowed_modes":
                    list(
                        m[
                            "allowed_modes"
                        ]
                    ),

                "generated_at":
                    _now(),

                "items":
                    qs,
            }
        )

        pdf_name = _name(
            m[
                "source_pdf"
            ],
            "source_pdf",
        )

        src = (
            folder
            /
            pdf_name
        )

        dst = (
            p[
                "sources"
            ]
            /
            "imported"
            /
            m[
                "package_id"
            ]
            /
            pdf_name
        )

        dst.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if (
            not dst.exists()
            or
            _sha(
                dst
            )
            !=
            _sha(
                src
            )
        ):
            shutil.copy2(
                src,
                dst,
            )

        seen.update(
            q[
                "id"
            ]
            for q
            in qs
        )

        staged[
            m[
                "package_id"
            ]
        ] = stage

        entry.update(
            {
                "published":
                    True,

                "errors":
                    [],

                "id_prefix":
                    m[
                        "id_prefix"
                    ],

                "question_count":
                    len(
                        qs
                    ),

                "questions_file":
                    str(
                        bank_path.relative_to(
                            p[
                                "root"
                            ]
                        )
                    ).replace(
                        "\\",
                        "/",
                    ),

                "questions_sha256":
                    _sha(
                        bank_path
                    ),

                "source_pdf":
                    str(
                        dst.relative_to(
                            p[
                                "root"
                            ]
                        )
                    ).replace(
                        "\\",
                        "/",
                    ),

                "source_pdf_sha256":
                    _sha(
                        dst
                    ),

                "official_utn_question":
                    m[
                        "official_utn_question"
                    ],

                "content_type":
                    m[
                        "content_type"
                    ],

                "allowed_modes":
                    list(
                        m[
                            "allowed_modes"
                        ]
                    ),

                "validation":
                    dict(
                        m[
                            "validation"
                        ]
                    ),
            }
        )

        published.append(
            entry
        )

    for pid, stage in staged.items():
        old_numbers = dict(
            reg.setdefault(
                "packages",
                {},
            ).get(
                pid,
                {},
            ).get(
                "numbers",
                {},
            )
        )

        old_numbers.update(
            stage[
                "numbers"
            ]
        )

        reg[
            "packages"
        ][
            pid
        ] = {
            "id_prefix":
                stage[
                    "id_prefix"
                ],

            "display_name":
                stage[
                    "display_name"
                ],

            "numbers":
                old_numbers,
        }

    reg[
        "updated_at"
    ] = _now()

    catalog = {
        "schema_version":
            CATALOG_SCHEMA,

        "generated_at":
            _now(),

        "legacy": {
            "question_count":
                len(
                    core
                ),

            "question_bank":
                "data/question_bank.json",

            "question_bank_sha256":
                _sha(
                    core_file
                ),

            "source_names":
                sorted(
                    {
                        str(
                            q.get(
                                "simulacro",
                                "",
                            )
                        ).strip()
                        for q
                        in core
                        if str(
                            q.get(
                                "simulacro",
                                "",
                            )
                        ).strip()
                    }
                ),
        },

        "published_packages":
            published,

        "blocked_packages":
            blocked,

        "published_question_count":
            sum(
                x[
                    "question_count"
                ]
                for x
                in published
            ),

        "runtime_total_question_count":
            len(
                core
            )
            +
            sum(
                x[
                    "question_count"
                ]
                for x
                in published
            ),
    }

    _write(
        p[
            "registry"
        ],
        reg,
    )

    _write(
        p[
            "catalog"
        ],
        catalog,
    )

    return catalog


def load_runtime_question_bank(
    core_file=None,
    project_root=None,
):
    p = _paths(
        project_root
    )

    core_path = (
        Path(
            core_file
        )
        if core_file is not None
        else
        p[
            "data"
        ]
        /
        "question_bank.json"
    )

    core = _read(
        core_path
    )

    if not isinstance(
        core,
        list,
    ):
        raise RuntimeError(
            "Banco original inválido"
        )

    try:
        catalog = refresh_catalog(
            p[
                "root"
            ]
        )

    except Exception:
        return list(
            core
        )

    out = list(
        core
    )

    seen = {
        str(
            q.get(
                "id",
                "",
            )
        ).strip()
        for q
        in core
    }

    for entry in catalog.get(
        "published_packages",
        [],
    ):
        try:
            path = (
                p[
                    "root"
                ]
                /
                entry[
                    "questions_file"
                ]
            )

            if (
                _sha(
                    path
                )
                !=
                entry[
                    "questions_sha256"
                ]
            ):
                continue

            payload = _read(
                path
            )

            if (
                payload.get(
                    "schema_version"
                )
                != RUNTIME_SCHEMA
                or
                payload.get(
                    "package_id"
                )
                != entry[
                    "package_id"
                ]
            ):
                continue

            items = payload.get(
                "items",
                [],
            )

            ids = [
                str(
                    q.get(
                        "id",
                        "",
                    )
                ).strip()
                for q
                in items
            ]

            if (
                len(
                    items
                )
                != entry[
                    "question_count"
                ]
                or
                any(
                    not x
                    for x
                    in ids
                )
            ):
                continue

            if (
                len(
                    ids
                )
                != len(
                    set(
                        ids
                    )
                )
                or
                any(
                    x in seen
                    for x
                    in ids
                )
            ):
                continue

            out.extend(
                items
            )

            seen.update(
                ids
            )

        except Exception:
            continue

    return out


def main():
    catalog = refresh_catalog(
        ROOT
    )

    bank = load_runtime_question_bank(
        ROOT
        /
        "data"
        /
        "question_bank.json",
        ROOT,
    )

    print(
        "=" * 72
    )

    print(
        "P2-E0 - CATÁLOGO DE CONTENIDO"
    )

    print(
        "=" * 72
    )

    print(
        "Extensiones publicadas :",
        len(
            catalog.get(
                "published_packages",
                [],
            )
        ),
    )

    print(
        "Extensiones bloqueadas :",
        len(
            catalog.get(
                "blocked_packages",
                [],
            )
        ),
    )

    print(
        "Preguntas runtime      :",
        len(
            bank
        ),
    )

    for item in catalog.get(
        "blocked_packages",
        [],
    ):
        print(
            "BLOQUEADO:",
            item.get(
                "package_id"
            )
            or
            item.get(
                "folder"
            ),
            "->",
            ", ".join(
                item.get(
                    "errors",
                    [],
                )
            ),
        )

    print(
        "RESULTADO: CATÁLOGO ACTUALIZADO Y VALIDADO"
    )


if __name__ == "__main__":
    main()
