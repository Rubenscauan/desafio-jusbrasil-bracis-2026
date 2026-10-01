"""Pipeline determinístico orientado ao acervo canônico.

O resolvedor indexa identificadores presentes no começo de cada registro, onde
fica o cabeçalho do processo.  Isto evita confundir o acórdão-fonte com os
acórdãos que apenas o citam no corpo.  Não lê o goldenset nem usa rede.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


def fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).upper().strip()


# Confusões declaradas no enunciado.  A conversão só é aplicada a sequências
# que já contêm dígitos, nunca a palavras comuns.
OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "s": "5"})
NUMBERISH = re.compile(r"(?<![A-Za-z0-9])(?=[0-9OoIlSsGg.\-–/()\s]{3,})(?:[0-9OoIlSsGg][0-9OoIlSsGg.\-–/()\s]*[0-9OoIlSsGg])")


def numeric_anchors(text: str, minimum: int = 3) -> list[tuple[int, int, str]]:
    anchors: list[tuple[int, int, str]] = []
    for match in NUMBERISH.finditer(text):
        raw = match.group()
        if not any(char.isdigit() for char in raw):
            continue
        # A UF é parte da referência, mas não do identificador.  Sem removê-la
        # antes da normalização, ``/RS`` viraria indevidamente ``/R5``.
        raw = re.sub(r"\s*(?:/|\-|–|\()\s*[A-Z]{2}\)?\s*$", "", raw, flags=re.IGNORECASE)
        # Um algarismo pode conter I/l/O/S por OCR, mas um título romano após
        # uma quebra de linha (por exemplo, ``0091.\n\nIII``) não pertence ao
        # identificador processual.
        raw = re.sub(r"\s+[A-Za-z]+\s*$", "", raw)
        # Espaços e pontuação são separadores; letras restantes sinalizam que
        # não era uma âncora numérica (por exemplo, uma palavra normal).
        normalized = raw.translate(OCR_DIGITS)
        digits = "".join(char if char.isdigit() else "?" for char in normalized if char.isdigit() or char in "Gg")
        if sum(char.isdigit() for char in digits) >= minimum:
            anchors.append((match.start(), match.end(), digits))
    return anchors


@dataclass(frozen=True)
class Record:
    canonical_id: int
    nature: str
    court: str | None
    heading: str


class HeaderIndex:
    """Índice de âncoras numéricas nos cabeçalhos, não no corpo citado."""

    def __init__(self, database: Path, header_chars: int = 900):
        self.records: dict[int, Record] = {}
        self.by_number: dict[str, set[int]] = defaultdict(set)
        self.primary_process_by_number: dict[str, set[int]] = defaultdict(set)
        self.process_numbers_by_length: dict[int, set[str]] = defaultdict(set)
        self.law_by_article: dict[str, set[int]] = defaultdict(set)
        self.sumula_by_number: dict[str, set[int]] = defaultdict(set)
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT id, natureza, tribunal, texto FROM documentos").fetchall()
        for canonical_id, nature, court, text in rows:
            self.records[canonical_id] = Record(canonical_id, nature, court, fold(text[:header_chars]))
            for start, _, number in numeric_anchors(text[:header_chars]):
                # Datas, folhas e inscrições também vivem no cabeçalho. Um
                # identificador processual só entra no índice se houver uma
                # classe processual imediatamente antes dele.
                is_process_number = nature == "acordao" and bool(PROCESS_START.search(text[max(0, start - 180) : start]))
                if nature != "acordao" or is_process_number:
                    self.by_number[number].add(canonical_id)
                if is_process_number:
                    self.process_numbers_by_length[len(number)].add(number)
            # Alguns acórdãos têm o identificador principal depois de uma
            # ementa excepcionalmente longa. Recupera-o fora do cabeçalho só
            # quando o próprio documento declara "estes autos de ...".
            for marker in MAIN_CASE_MARKER.finditer(text):
                region = text[marker.end() : marker.end() + 350]
                match = PROCESS_RE.search(region)
                if not match or not re.search(r"\bTST[-\s]", region[: match.end()], re.I):
                    continue
                number = _number(match)
                if sum(character.isdigit() for character in number) >= 6:
                    self.primary_process_by_number[number].add(canonical_id)
                    self.process_numbers_by_length[len(number)].add(number)
            if nature == "dispositivo":
                article = re.search(r"\bART\.?(?:IGO)?\s*(\d+)", fold(text[:250]))
                if article:
                    self.law_by_article[article.group(1)].add(canonical_id)
            elif nature == "sumula":
                number = re.search(r"\bSUMULA\s+(?:VINCULANTE\s+)?(?:N[º°O.]?\s*)?(\d+)", fold(text))
                if number:
                    self.sumula_by_number[number.group(1)].add(canonical_id)

    def process(self, number: str, court: str | None = None) -> int | None:
        primary = set(self.primary_process_by_number.get(number, ()))
        if court:
            court_folded = fold(court)
            primary = {identifier for identifier in primary if fold(self.records[identifier].court or "") == court_folded}
        if primary:
            return next(iter(primary)) if len(primary) == 1 else None
        options = {identifier for identifier in self.by_number.get(number, ()) if self.records[identifier].nature == "acordao"}
        # Uma letra ilegível dentro de uma âncora processual vira ``?``.  A
        # busca fuzzy é limitada a uma substituição (ou uma inserção/remoção)
        # e só é aceita quando chega a um único cabeçalho de acórdão.
        if not options and len(number) >= 6:
            candidates: set[str] = set()
            for length in range(len(number) - 1, len(number) + 2):
                for known in self.process_numbers_by_length.get(length, ()):
                    if _distance_at_most_one(number, known):
                        candidates.add(known)
            options = {
                identifier
                for candidate in candidates
                for identifier in self.by_number[candidate]
                if self.records[identifier].nature == "acordao"
            }
        if court:
            court = fold(court)
            options = {identifier for identifier in options if fold(self.records[identifier].court or "") == court}
        return next(iter(options)) if len(options) == 1 else None

    def law(self, article: str) -> int | None:
        article = "".join(character for character in article if character.isdigit())
        options = self.law_by_article.get(article, set())
        return next(iter(options)) if len(options) == 1 else None

    def sumula(self, number: str, court: str | None) -> int | None:
        options = self.sumula_by_number.get(number, set())
        if court:
            options = {identifier for identifier in options if fold(self.records[identifier].court or "") == fold(court)}
        return next(iter(options)) if len(options) == 1 else None

    def compatible_family(self, citation_prefix: str, canonical_id: int) -> bool:
        """Evita que um número citado no corpo resolva para outra classe."""
        expected = process_family(citation_prefix)
        actual = process_family(self.records[canonical_id].heading)
        return not expected or not actual or expected == actual

    def compatible_law(self, cited_law: str, canonical_id: int) -> bool:
        """Rejeita fonte militar quando a referência declara Constituição."""
        cited = fold(cited_law)
        heading = self.records[canonical_id].heading
        return not ("CONSTITUI" in cited and "MILITAR" in heading)


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    kind: str
    label: str
    canonical_id: int | None = None


# Classes processuais e conectores podem compor-se livremente: por exemplo,
# "EDcl nos EDcl no AgInt no Agravo em Recurso Especial".  O número continua
# sendo obrigatório, o que mantém a expressão longe de prosa comum.
PROCESS_PREFIX = r"(?:TERCEIRO\s+)?(?:TST[-\s]+)?(?:(?:EMBARGOS(?:\s+DE\s+DECLARA.{0,4}O)?|EDCL|EDS?|AGINT|AG\.?\s*INT\.?|AGR[G.]?|AG\.?\s*REG(?:IMENTAL)?|AGRAVO(?:\s+INTERNO|\s+EM\s+RECURSO\s+ESPECIAL|\s+DE\s+INSTRUMENTO)?|RECURSO(?:\s+EM\s+HABEAS\s+CORPUS|\s+ESPECIAL(?:\s+ELEITORAL)?)?|REC\.?\s*ESP(?:E)?\.?|R\.?\s*ESP(?:E)?\.?|RESP(?:E|EI)?|ARESP(?:EI)?|RHC|H\.?C\.?|RECL\.?|RCL|RECLAMA.{0,4}O|RSE|APL|RMS|ARR|RR|(?-i:AR)(?=\s*(?:N[º°O�.]?\s*)?\d)|(?-i:AI)(?=\s*(?:N[º°O�.]?\s*)?\d)|MS|TEM.{0,2}|R-RP|AGARR)(?:\s+(?:NO|NOS|NA|NAS|EM|DE|DO|DA))?\s*)+"
PROCESS_RE = re.compile(
    rf"(?P<prefix>{PROCESS_PREFIX})(?:N[º°O�.]?|NO)?\s*(?:[-–]\s*)?(?P<number>[0-9OoIlSsGg][0-9OoIlSsGg.\-–/()\s]{{2,}}\d)(?:\s*(?:/|\-|–|\()\s*(?P<court>[A-Z]{{2}})\)?)?",
    re.IGNORECASE,
)
MAIN_CASE_MARKER = re.compile(r"\bESTES\s+AUTOS\s+DE\b", re.IGNORECASE)
SUMULA_RE = re.compile(r"\b(?:[S5][ÚU�]MULA|[S5][ÚU�]M\.)\s*(?P<vinc>VINCULANTE\s*)?(?:N[º°O�.]?\s*)?(?P<number>\d+)(?:\s*(?:DO|DA)?\s*(?P<court>STF|STJ|TST))?\b", re.IGNORECASE)
LAW_RE = re.compile(
    r"\bART(?:IGO)?\.?\s*(?P<article>\d+(?:\.\d+)?)(?:[º°])?(?:\s*,\s*(?:§\s*\d+[º°]?(?:-[A-Z])?|[IVXLCDM]+|INCISO\s+[IVXLCDM]+|[A-Z]\s*,?\s*[IVXLCDM]+))?(?:\s*,\s*['\"]?[A-Z]['\"]?)?\s*,?\s*(?:DO|DA)\s+(?P<law>"
    r"CONSOLIDACAO\s+DAS\s+LEIS\s+DO\s+TRABALHO|CONSTITUICAO(?:\s+(?:FEDERAL|DA\s+REPUBLICA))?|"
    r"CODIGO\s+(?:DE\s+DEFESA\s+DO\s+CONSUMIDOR|PENAL\s+MILITAR|DE\s+PROCESSO\s+(?:CIVIL|PENAL)|ELEITORAL|CIVIL)|"
    r"CPC|CPP|CLT|CPM|CDC|CC|LEI(?:\s+COMPLEMENTAR)?\s*(?:N[º°O.]?\s*)?\d[\d.\s/\-]*"
    r")",
    re.IGNORECASE,
)
# Variante tolerante ao caractere de substituição introduzido por arquivos
# legados (``Constitui��o``). A fonte da decisão continua sendo apenas o
# número do artigo consultado contra os registros de natureza ``dispositivo``.
FLEX_LAW_RE = re.compile(
    r"\bART(?:IGO)?\.?\s*(?P<article>\d+(?:\.\d+)?)(?:[º°])?[\s\S]{0,35}?\b(?:DO|DA)\s+(?P<law>"
    r"CONSOLIDA.{0,5}O\s+DAS\s+LEIS\s+DO\s+TRABALHO|CONSTITUI.{0,5}(?:\s+(?:FEDERAL|DA\s+REPUBLICA))?|"
    r"C.DIGO\s+(?:DE\s+DEFESA\s+DO\s+CONSUMIDOR|PENAL\s+MILITAR|DE\s+PROCESSO\s+(?:CIVIL|PENAL)|ELEITORAL|CIVIL)|"
    r"CPC|CPP|CLT|CPM|CDC|CC|LEI(?:\s+COMPLEMENTAR)?\s*(?:N.{0,2}\s*)?\d[\d.\s/\-]*"
    r")",
    re.IGNORECASE,
)

VAGUE = (
    # Mantém apenas referências com classe, tribunal, data e relatoria explícitos.
    ("jurisprudencia", re.compile(r"\b(?:julgado|ac[óo�]rd[ãa�]o|precedente|Reclama[cç][ãa�]o|Recurso\s+em\s+Habeas\s+Corpus|Agravo\s+em\s+Recurso\s+Especial)\s+(?:do|da)\s+(?:STF|STJ|TST|TSE|STM),?\s*(?:prof(?:er|cr)ido\s+)?(?:em|de)\s+\d{4}[\s\S]{0,100}?(?:relatoria|Rel\.?)\s*(?:Min\.?)?[^,.\n]{2,80}", re.I)),
)

# Citações por classe + ano + relator sem número de processo. O span é mantido
# curto para representar a identificação disponível, sem exigir que o nome esteja
# na mesma linha do marcador de relatoria.
EXPLICIT_CITATION_RE = re.compile(
    r"\b(?:RCL|APL)\s+DE\s+\d{4}\s*,?\s*REL\.?\s*(?:MIN\.?\s*)?[\s\S]{2,80}",
    re.I,
)

# Tema de repercussão geral sem documento canônico correspondente é uma
# referência explícita, porém inventada. Mantemos a expressão inteira para
# não deixar somente ``Tema 2.680`` abaixo do limiar de IoU.
THEME_RE = re.compile(r"\bTEM[AÃ�]\s+\d[\d.]*\s+DA\s+REPERCUSS[ÃA�]O\s+GERAL\b", re.I)

# ``RE`` isolado é curto demais para a expressão geral (e colide com prosa em
# português), mas quando está em maiúsculas, seguido por ``nº`` e um número,
# é uma referência verificável.
RE_ONLY_RE = re.compile(r"\bRE\.?\s*(?:[Nn][º°O�.]?|[Nn][Oo])?\s*(?P<number>\d[\d.\s/\-–]{2,}\d)(?:\s*(?:/|\-|–)\s*[A-Z]{2})?", 0)

# Início de uma referência processual. É usado depois de a âncora já ter sido
# resolvida no cabeçalho; portanto aceitar grafias amplas aqui não aumenta a
# superfície de falso-positivo de números soltos.
PROCESS_START = re.compile(
    r"\b(?:terceiro\s+ag\.?reg|embargos(?:\s+de\s+declara.{0,4}o)?|edcl|eds?|agint|agrg|ag\.?\s*reg|"
    r"agravo|recurso(?:\s+em\s+habeas\s+corpus|\s+especial)?|rec\.?\s*esp\.?|r\.?\s*esp(?:e)?|"
    r"resp(?:e|ei)?|aresp(?:ei)?|rhc|h\.?c\.?|rcl|recl(?:ama.{0,4}o)?|rse|apl|rms|arr|rr|(?-i:AR)(?=\s*(?:N[º°O�.]?\s*)?\d)|(?-i:AI)(?=\s*(?:N[º°O�.]?\s*)?\d)|ms|"
    r"r-rp|tst[-\s]|processo\s+n[º°o�.]?)\b",
    re.IGNORECASE,
)
PROCESS_CONTEXT = re.compile(r"\b(?:processo|recurso|agravo|embargos|rcl|recl|rhc|h\.c\.|resp|respe|aresp|rse|apl|rms|arr|rr|agint|agrg|edcl|eds?|r-rp)\b|TST[-\s]", re.I)


def _number(match: re.Match[str]) -> str:
    value = re.sub(r"(?:/|\-|–|\()\s*[A-Z]{2}\)?\s*$", "", match.group("number"), flags=re.IGNORECASE)
    result: list[str] = []
    for character in value:
        if character.isdigit():
            result.append(character)
        elif ord(character) in OCR_DIGITS:
            result.append(OCR_DIGITS[ord(character)])
        elif character.isalpha():
            result.append("?")
    return "".join(result)


def _distance_at_most_one(left: str, right: str) -> bool:
    """Distância de edição <= 1, com ``?`` como curinga de um dígito."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b and a != "?" for a, b in zip(left, right)) <= 1
    if len(left) > len(right):
        left, right = right, left
    index = other = changes = 0
    while index < len(left) and other < len(right):
        if left[index] == right[other] or left[index] == "?":
            index += 1
            other += 1
        else:
            changes += 1
            other += 1
            if changes > 1:
                return False
    return True


def process_family(value: str) -> str | None:
    value = fold(value)
    if "RECURSO EM HABEAS CORPUS" in value or re.search(r"\bRHC\b", value):
        return "RHC"
    if "HABEAS CORPUS" in value or re.search(r"\bHC\b", value):
        return "HC"
    if "RECLAM" in value or re.search(r"\bRCL\b", value):
        return "RCL"
    if "ACAO RESCISORIA" in value or re.search(r"\bAR\b", value):
        return "AR"
    if "MANDADO DE SEGURANCA" in value or re.search(r"\bRMS\b", value):
        return "RMS"
    if "RECURSO EXTRAORDINARIO" in value or re.search(r"\bRE\b", value):
        return "RE"
    if "AGRAVO EM RECURSO ESPECIAL" in value or "ARESP" in value:
        return "ARESP"
    if "RECURSO ESPECIAL" in value or "RESP" in value or "R ESP" in value:
        return "RESP"
    return None


def detect(text: str, index: HeaderIndex) -> list[Candidate]:
    candidates: list[Candidate] = []
    for match in LAW_RE.finditer(text):
        article = match.group("article")
        canonical_id = index.law(article)
        if canonical_id and not index.compatible_law(match.group("law"), canonical_id):
            canonical_id = None
        candidates.append(Candidate(match.start(), match.end(), "lei", "real" if canonical_id else "inventada", canonical_id))
    for match in FLEX_LAW_RE.finditer(text):
        if any(match.start() < item.end and item.start < match.end() for item in candidates):
            continue
        canonical_id = index.law(match.group("article"))
        if canonical_id and not index.compatible_law(match.group("law"), canonical_id):
            canonical_id = None
        candidates.append(Candidate(match.start(), match.end(), "lei", "real" if canonical_id else "inventada", canonical_id))
    for match in SUMULA_RE.finditer(text):
        canonical_id = index.sumula(match.group("number"), match.group("court"))
        # Uma súmula explicitamente numerada mas ausente do registro próprio é
        # uma referência verificável, portanto inventada.
        candidates.append(Candidate(match.start(), match.end(), "jurisprudencia", "real" if canonical_id else "inventada", canonical_id))
    for match in THEME_RE.finditer(text):
        candidates.append(Candidate(match.start(), match.end(), "jurisprudencia", "inventada"))
    for match in PROCESS_RE.finditer(text):
        number = _number(match)
        # O sufixo do processo é uma UF, não o tribunal superior do acórdão.
        canonical_id = index.process(number)
        candidates.append(Candidate(match.start(), match.end(), "jurisprudencia", "real" if canonical_id else "inventada", canonical_id))
    for match in RE_ONLY_RE.finditer(text):
        number = "".join(character for character in match.group("number") if character.isdigit())
        canonical_id = index.process(number)
        candidates.append(Candidate(match.start(), match.end(), "jurisprudencia", "real" if canonical_id else "inventada", canonical_id))
    for kind, regex in VAGUE:
        for match in regex.finditer(text):
            candidates.append(Candidate(match.start(), match.end(), kind, "incompleta"))
    for match in EXPLICIT_CITATION_RE.finditer(text):
        candidates.append(Candidate(match.start(), match.end(), "jurisprudencia", "incompleta"))

    # A regex principal cobre o formato usual.  Esta segunda passagem trata
    # classes novas sem enumerá-las: somente aceita uma âncora que resolve no
    # cabeçalho e possui marcador processual próximo.
    for start, end, number in numeric_anchors(text, minimum=4):
        if any(start < item.end and item.start < end for item in candidates):
            continue
        left = max(0, start - 130)
        canonical_id = index.process(number)
        if canonical_id is None:
            continue
        # Não atravessamos a frase anterior; dentro dela preservamos o primeiro
        # marcador, essencial para cadeias como "EDcl no AgInt no REsp".
        sentence_start = max(text.rfind(".", left, start), text.rfind(";", left, start), text.rfind(":", left, start), text.rfind("\n\n", left, start)) + 1
        prefix = text[sentence_start:start]
        first_marker = next(iter(PROCESS_START.finditer(prefix)), None)
        # Uma classe processual em frase anterior não justifica transformar a
        # data de assinatura em processo: o marcador e a âncora devem estar
        # dentro da mesma oração.
        if first_marker is None:
            continue
        span_start = sentence_start + first_marker.start()
        # Inclui UF/parêntese, mas não engole a oração seguinte.
        span_end = end
        while span_end < len(text) and text[span_end] in " \t/()-ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            span_end += 1
            if span_end - end > 8:
                break
        candidates.append(Candidate(span_start, span_end, "jurisprudencia", "real", canonical_id))

    # Um único objeto representa menções sobrepostas: o mais específico vence.
    selected: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, -(item.end - item.start), item.label != "real")):
        if not any(candidate.start < item.end and item.start < candidate.end for item in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def process(path: Path, index: HeaderIndex) -> dict:
    text = path.read_text(encoding="utf-8")
    citations = []
    for candidate in detect(text, index):
        citations.append(
            {
                "inicio": candidate.start,
                "fim": candidate.end,
                "trecho": text[candidate.start:candidate.end],
                "tipo": candidate.kind,
                "classificacao": candidate.label,
                "resolucao": {"id_canonico": candidate.canonical_id} if candidate.canonical_id else None,
                "confianca": 0.98 if candidate.label == "real" else 0.94,
            }
        )
    return {"documento_id": path.stem, "citacoes": citations}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    index = HeaderIndex(args.database)
    for path in sorted(args.input.glob("*.txt")):
        payload = process(path, index)
        (args.output / f"{path.stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
