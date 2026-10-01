"""Baseline determinístico para o desafio Caça-Alucinações.

Não consulta fontes externas: toda resolução é feita em desafio1_bracis.db.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from number_position import NumberPositionIndex, citation_envelope
from semantic_detector import candidates as semantic_candidates, fingerprint as semantic_fingerprint


def fold(value: str) -> str:
    """Forma comparável: sem acentos, caixa alta e com OCR previsível."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.upper().replace("5UMULA", "SUMULA")
    return re.sub(r"\s+", " ", value).strip()


def digits(value: str) -> str:
    return "".join(re.findall(r"\d", value))


def normalized_class(value: str) -> str:
    value = fold(value)
    value = re.sub(r"\bREC(?:URSO)?\.?\s*ESP(?:ECIAL)?\.?\b", "RESP", value)
    value = re.sub(r"\bAGRAVO\s+EM\s+RECURSO\s+ESPECIAL\b", "ARESP", value)
    value = re.sub(r"\bRECLAMACAO\b", "RCL", value)
    value = re.sub(r"\bHABEAS\s+CORPUS\b", "HC", value)
    value = re.sub(r"\bRECURSO\s+EM\s+HABEAS\s+CORPUS\b", "RHC", value)
    value = re.sub(r"\bSUMULA\s+VINCULANTE\b", "SV", value)
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    text: str
    kind: str
    number: str | None = None
    court: str | None = None
    uf: str | None = None
    resolved_id: int | None = None


PROCESS_CLASS = r"""
    (?:AGINT(?:\s+NO)?|AGRG(?:\s+NO)?|EDCL(?:\s+NOS?)?(?:\s+EDCL)?(?:\s+NO)?\s+AGINT(?:\s+NO)?|
       A(?:GRAVO)?\.?\s*R(?:EGIMENTAL)?\.?|ARESP|RESP|R\.?ESP\.?|RECURSO\s+ESPECIAL|
       RECURSO\s+EM\s+HABEAS\s+CORPUS|RHC|HABEAS\s+CORPUS|HC|RECLAMA.{0,3}O|RCL|
       A(?:G|GRAVO)?R?\-?RESPE|RESPE|ARE[S5]PEI|RECURSO\s+ESPECIAL\s+ELEITORAL|
       RECURSO\s+EXTRAORDINARIO|RE|RSE|APL|AI|MS)
"""

# O número precisa começar e terminar em dígito; aceita pontuação, espaços e quebras.
PROCESS_RE = re.compile(
    rf"(?P<class>{PROCESS_CLASS})\s*(?:N[º°O�]|NO|N\.)?\s*"
    r"(?P<number>\d[\d.\s\-/–]{1,}\d|\d{3,})(?:\s*(?:/|\-|–|\()\s*(?P<uf>[A-Z]{2})\)?)?",
    re.IGNORECASE | re.VERBOSE,
)
SUMULA_RE = re.compile(
    r"\b(?P<sv>[5S][UÚ�]MULA\s+VINCULANTE|[5S][UÚ�]MULA)\s*(?:n[º°o�.]?\s*)?(?P<number>\d+)(?:\s*(?:do|da)?\s*(?P<court>STF|STJ|TST))?\b",
    re.IGNORECASE,
)
LAW_RE = re.compile(
    r"\b(?:art(?:igo)?\.?\s*)(?P<number>\d+(?:[º°])?)(?:\s*,?\s*(?:inciso\s+)?(?P<item>[IVXLCDM]+))?\s*,?\s*(?:do|da)\s+(?P<law>CPC|CPP|CC|CLT|CPM|CDC|CF(?:/88)?|CONSTITUI[CÇ][AÃ]O(?:\s+FEDERAL)?|C[ÓO]DIGO\s+(?:DE\s+DEFESA\s+DO\s+CONSUMIDOR|PENAL\s+MILITAR|DE\s+PROCESSO\s+CIVIL|ELEITORAL))\b",
    re.IGNORECASE,
)
VAGUE_CASE_RE = re.compile(
    r"\b(?:julgado|ac[óo�]rd[ãa�]o|precedente)\s+(?:do|da)\s+(?P<court>STF|STJ|TST|TSE|STM)\s+(?:proferido\s+)?(?:em|de)\s+\d{4}[^.\n]{0,100}(?:relatoria|relator)[^,.\n]{0,80}",
    re.IGNORECASE,
)
VAGUE_LAW_RE = re.compile(r"\b(?:normas?|dispositivo)\s+(?:legais?\s+)?de\s+reg[êe�]ncia(?:\s+da\s+mat[ée�]ria)?\b", re.IGNORECASE)
VAGUE_REFERENCE_RE = re.compile(
    r"\b(?:jurisprud[êe�]ncia\s+pac[íi�]fica(?:\s+desta\s+Corte)?|entendimento\s+sumulado\s+sobre\s+a\s+mat[ée�]ria|"
    r"precedentes?\s+(?:desta\s+Casa|em\s+situa[cç][õo�]es\s+an[áa�]logas)|"
    r"dispositivo\s+constitucional\s+invocado\s+na\s+origem|artigo\s+correspondente\s+do\s+C[óo�]digo\s+de\s+Processo\s+Civil|"
    r"Rcl\s+de\s+\d{4}\s*,?\s*Rel\.\s*Min\.[^,.\n]+)",
    re.IGNORECASE,
)


def candidates(text: str) -> list[Candidate]:
    found: list[Candidate] = []
    for match in PROCESS_RE.finditer(text):
        raw_number = match.group("number")
        # Números curtos são ruído comum; classes processuais com menos de 3 dígitos não ocorrem aqui.
        if len(digits(raw_number)) < 3:
            continue
        found.append(Candidate(match.start(), match.end(), match.group(), "jurisprudencia", digits(raw_number), uf=match.group("uf")))
    for match in SUMULA_RE.finditer(text):
        found.append(Candidate(match.start(), match.end(), match.group(), "jurisprudencia", match.group("number"), match.group("court")))
    for match in LAW_RE.finditer(text):
        found.append(Candidate(match.start(), match.end(), match.group(), "lei", match.group("number"), fold(match.group("law"))))
    for regex, kind in ((VAGUE_CASE_RE, "jurisprudencia"), (VAGUE_LAW_RE, "lei"), (VAGUE_REFERENCE_RE, "jurisprudencia")):
        for match in regex.finditer(text):
            inferred_kind = "lei" if "DISPOSITIVO" in fold(match.group()) or "ARTIGO CORRESPONDENTE" in fold(match.group()) else kind
            found.append(Candidate(match.start(), match.end(), match.group(), inferred_kind))

    # Mantém a menção mais específica quando padrões se sobrepõem.
    result: list[Candidate] = []
    for item in sorted(found, key=lambda x: (x.start, -(x.end - x.start))):
        if not any(item.start < saved.end and saved.start < item.end for saved in result):
            result.append(item)
    return result


@dataclass(frozen=True)
class Record:
    canonical_id: int
    court: str | None
    nature: str


class CanonicalIndex:
    def __init__(self, database: Path):
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT id, tribunal, natureza, texto FROM documentos"
            ).fetchall()
        self.records = [
            Record(row[0], row[1], row[2])
            for row in rows
        ]
        self.by_id = {record.canonical_id: record for record in self.records}

    def resolve(
        self, candidate: Candidate, number_index: NumberPositionIndex
    ) -> list[Record]:
        if not candidate.number:
            return []
        if candidate.kind == "jurisprudencia":
            if candidate.text.upper().replace("5", "S").startswith(("SÚMULA", "SUMULA")):
                target_nature = "sumula"
            else:
                target_nature = "acordao"
        else:
            target_nature = "dispositivo"

        number = digits(candidate.number)
        matches: list[tuple[int, Record]] = []
        for canonical_id, position in number_index.occurrences.get(number, {}).items():
            record = self.by_id[canonical_id]
            if record.nature != target_nature:
                continue
            if candidate.court and record.court and fold(candidate.court) != fold(record.court):
                continue
            matches.append((position, record))
        return [record for _, record in sorted(matches, key=lambda item: item[0])]


def classify(
    candidate: Candidate, index: CanonicalIndex, number_index: NumberPositionIndex
) -> tuple[str, int | None, float]:
    # Descrições vagas são explicitamente incompletas no enunciado.
    if candidate.number is None:
        return "incompleta", None, 0.98
    if candidate.resolved_id is not None:
        return "real", candidate.resolved_id, 0.98
    matches = index.resolve(candidate, number_index)
    unique = {record.canonical_id for record in matches}
    if len(unique) == 1:
        return "real", next(iter(unique)), 0.98
    if len(unique) > 1:
        # A mesma referência pode aparecer como citação em vários acórdãos.
        # A ocorrência mais precoce é um sinal genérico de documento-fonte,
        # enquanto menções posteriores tendem a ser referências internas.
        return "real", matches[0].canonical_id, 0.80
    return "inventada", None, 0.92


def process_document(
    path: Path,
    index: CanonicalIndex,
    number_index: NumberPositionIndex,
    repeated_semantic: set[str],
) -> dict:
    text = path.read_text(encoding="utf-8")
    detected = candidates(text)
    number_matches = number_index.unique_long_number_matches(text)
    number_matches += number_index.unique_marked_number_matches(text)
    for number_start, number_end, canonical_id, number in number_matches:
        start, end = citation_envelope(text, number_start, number_end, number)
        # Se a regra de cobertura já achou essa citação, preservamos seus limites.
        if any(start < item.end and item.start < end for item in detected):
            continue
        detected.append(
            Candidate(
                start, end, text[start:end], "jurisprudencia", number,
                resolved_id=canonical_id,
            )
        )

    # Referências vagas não têm identificador para consultar. A similaridade
    # textual propõe poucas janelas; a sobreposição evita duplicar o detector
    # numérico/determinístico já mais preciso.
    for semantic in semantic_candidates(text):
        if semantic_fingerprint(text[semantic.start : semantic.end]) in repeated_semantic:
            continue
        if any(semantic.start < item.end and item.start < semantic.end for item in detected):
            continue
        detected.append(
            Candidate(
                semantic.start,
                semantic.end,
                text[semantic.start : semantic.end],
                semantic.kind,
            )
        )

    items = []
    for candidate in sorted(detected, key=lambda item: item.start):
        label, canonical_id, confidence = classify(candidate, index, number_index)
        items.append(
            {
                "inicio": candidate.start,
                "fim": candidate.end,
                "trecho": text[candidate.start:candidate.end],
                "tipo": candidate.kind,
                "classificacao": label,
                "resolucao": {"id_canonico": canonical_id} if canonical_id else None,
                "confianca": confidence,
            }
        )
    return {"documento_id": path.stem, "citacoes": items}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    index = CanonicalIndex(args.database)
    number_index = NumberPositionIndex(args.database)
    paths = sorted(args.input.glob("*.txt"))
    semantic_frequency: Counter[str] = Counter()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        fingerprints = {
            semantic_fingerprint(text[item.start : item.end])
            for item in semantic_candidates(text)
        }
        semantic_frequency.update(fingerprints)
    repeated_semantic = {
        value for value, frequency in semantic_frequency.items() if frequency >= 2
    }
    for path in paths:
        payload = process_document(path, index, number_index, repeated_semantic)
        (args.output / f"{path.stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
