"""Combina a cobertura do baseline com resolução por cabeçalho canônico.

O módulo preserva o detector conservador existente e substitui apenas a etapa
de resolução; novas regras acrescentam padrões explicitamente jurídicos que o
detector antigo não reconhecia.  Nenhuma parte lê o goldenset.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import main as baseline

from pipeline_v2 import EXPLICIT_CITATION_RE, FLEX_LAW_RE, HeaderIndex, LAW_RE, PROCESS_RE, PROCESS_START, SUMULA_RE, VAGUE, detect, numeric_anchors, process_family


def refresh(item: dict, text: str, index: HeaderIndex) -> None:
    """Recalcula tipo, classe e doc_id quando o trecho é verificável."""
    span = text[item["inicio"] : item["fim"]]
    law = LAW_RE.search(span) or FLEX_LAW_RE.search(span)
    if law:
        canonical_id = index.law(law.group("article"))
        if canonical_id and not index.compatible_law(law.group("law"), canonical_id):
            canonical_id = None
        item["tipo"] = "lei"
        item["classificacao"] = "real" if canonical_id else "inventada"
        item["resolucao"] = {"id_canonico": canonical_id} if canonical_id else None
        item["confianca"] = 0.98
        return
    sumula = SUMULA_RE.search(span)
    if sumula:
        canonical_id = index.sumula(sumula.group("number"), sumula.group("court"))
        item["tipo"] = "jurisprudencia"
        item["classificacao"] = "real" if canonical_id else "inventada"
        item["resolucao"] = {"id_canonico": canonical_id} if canonical_id else None
        item["confianca"] = 0.98
        return
    # Ano + relator é deliberadamente uma referência ambígua no contrato,
    # mesmo quando algum ano aparece por acaso no acervo canônico.
    if explicit_vague(span):
        item["classificacao"] = "incompleta"
        item["resolucao"] = None
        item["confianca"] = 0.78
        return
    process = PROCESS_RE.search(span)
    resolved = (item.get("resolucao") or {}).get("id_canonico")
    if process and item["classificacao"] == "real" and resolved and not index.compatible_family(process.group("prefix"), resolved):
        item["classificacao"] = "inventada"
        item["resolucao"] = None
        item["confianca"] = 0.95
        return
    anchors = numeric_anchors(span, minimum=4)
    if anchors and PROCESS_START.search(span):
        canonical_id = index.process(max(anchors, key=lambda value: len(value[2]))[2])
        if canonical_id:
            item["tipo"] = "jurisprudencia"
            item["classificacao"] = "real"
            item["resolucao"] = {"id_canonico": canonical_id}
            item["confianca"] = 0.98


def explicit_vague(span: str) -> bool:
    """Aceita somente menções vagas com estrutura jurídica reconhecível."""
    if any(regex.search(span) for _, regex in VAGUE):
        return True
    # Menções por classe, ano e relator são explícitas, porém não identificam
    # univocamente o processo; o padrão também não atravessa parágrafos.
    return bool(EXPLICIT_CITATION_RE.search(span))


def confidence(item: dict, span: str) -> float:
    """Probabilidade conservadora derivada de sinais verificáveis locais."""
    if item["classificacao"] == "incompleta":
        return 0.78 if explicit_vague(span) else 0.60
    if item["classificacao"] == "inventada":
        return 0.95 if numeric_anchors(span, minimum=4) else 0.82
    if LAW_RE.search(span) or FLEX_LAW_RE.search(span) or SUMULA_RE.search(span):
        return 0.985
    anchors = numeric_anchors(span, minimum=4)
    return 0.995 if anchors and len(max(anchors, key=lambda value: len(value[2]))[2]) >= 10 else 0.975


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    canonical = baseline.CanonicalIndex(args.database)
    number_index = baseline.NumberPositionIndex(args.database)
    header_index = HeaderIndex(args.database)
    paths = sorted(args.input.glob("*.txt"))
    frequencies: Counter[str] = Counter()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        frequencies.update({baseline.semantic_fingerprint(text[item.start : item.end]) for item in baseline.semantic_candidates(text)})
    repeated = {value for value, count in frequencies.items() if count >= 2}

    args.output.mkdir(parents=True, exist_ok=True)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        payload = baseline.process_document(path, canonical, number_index, repeated)
        citations = payload["citacoes"]
        for item in citations:
            refresh(item, text, header_index)
        citations[:] = [
            item for item in citations
            if not (
                # O gerador semântico é útil apenas como apoio a padrões
                # explícitos. Sem essa trava ele transforma cabeçalhos e datas
                # em "citações" de baixa precisão.
                item["classificacao"] == "incompleta"
                and not numeric_anchors(text[item["inicio"] : item["fim"]], minimum=3)
                and not explicit_vague(text[item["inicio"] : item["fim"]])
            )
            and not (
                item["classificacao"] in {"real", "inventada"}
                and item["tipo"] == "jurisprudencia"
                and not (
                    SUMULA_RE.search(text[item["inicio"] : item["fim"]])
                    or PROCESS_START.search(text[item["inicio"] : item["fim"]])
                )
            )
        ]

        # Acrescenta apenas novas referências que não se sobrepõem a uma saída
        # existente. Isso mantém as fronteiras já comprovadamente estáveis do
        # baseline e evita duplicar menções.
        for candidate in detect(text, header_index):
            overlapping = [item for item in citations if candidate.start < item["fim"] and item["inicio"] < candidate.end]
            if overlapping:
                # A etapa por cabeçalho pode recuperar uma cadeia que o regex
                # antigo cortou no último "REsp". Substituímos somente quando
                # ela é uma resolução real e contém mais contexto jurídico.
                current = overlapping[0]
                current_span = text[current["inicio"] : current["fim"]]
                current_starts_with_process = bool(PROCESS_START.match(current_span.lstrip()))
                if not (
                    (
                        candidate.label == "real"
                        and (
                            candidate.end - candidate.start > current["fim"] - current["inicio"]
                            or not current_starts_with_process
                        )
                    )
                    or (
                        candidate.label == "inventada"
                        and current["classificacao"] in {"incompleta", "inventada"}
                        and candidate.end - candidate.start > current["fim"] - current["inicio"]
                    )
                ):
                    continue
                citations.remove(current)
            citations.append(
                {
                    "inicio": candidate.start,
                    "fim": candidate.end,
                    "trecho": text[candidate.start : candidate.end],
                    "tipo": candidate.kind,
                    "classificacao": candidate.label,
                    "resolucao": {"id_canonico": candidate.canonical_id} if candidate.canonical_id else None,
                    "confianca": 0.98 if candidate.label == "real" else 0.94,
                }
            )
        citations.sort(key=lambda item: item["inicio"])
        for item in citations:
            span = text[item["inicio"] : item["fim"]]
            # Referências incompletas por classe/ano/relatoria não podem
            # engolir a seção seguinte quando a relatoria termina antes dela.
            if item["classificacao"] == "incompleta" and re.search(r"\bAPL\s+DE\s+\d{4}", span, re.I):
                paragraph = re.search(r"\r?\n[ \t]*\r?\n", span)
                if paragraph:
                    item["fim"] = item["inicio"] + paragraph.start()
                    item["trecho"] = text[item["inicio"] : item["fim"]]
                    span = item["trecho"]

            if item["classificacao"] == "real" and item["tipo"] == "jurisprudencia":
                process_matches = [
                    match for match in PROCESS_RE.finditer(text)
                    if match.end() > item["inicio"]
                    and match.start() < item["fim"]
                    and item["inicio"] - 120 <= match.start()
                    and match.end() <= item["fim"] + 2
                ]
                if process_matches:
                    process = max(process_matches, key=lambda match: match.end() - match.start())
                    process_start = process.start()
                    prefix_window_start = max(0, process_start - 80)
                    prefix_window = text[prefix_window_start:process_start]
                    explicit_process_label = re.search(
                        r"\bPROCESSO\s+N[º°O.]?\s+TST[-\s]*[A-Z\s.-]{0,40}$",
                        prefix_window,
                        re.I,
                    )
                    if explicit_process_label:
                        process_start = prefix_window_start + explicit_process_label.start()
                    item["inicio"], item["fim"] = process_start, process.end()
                    item["trecho"] = text[item["inicio"] : item["fim"]]
                    canonical_id = (item.get("resolucao") or {}).get("id_canonico")
                    if (
                        process_family(process.group("prefix")) == "RCL"
                        and canonical_id in header_index.records
                        and process_family(header_index.records[canonical_id].heading) != "RCL"
                    ):
                        item["classificacao"] = "inventada"
                        item["resolucao"] = None
                        item["confianca"] = 0.95
            item["confianca"] = confidence(item, text[item["inicio"] : item["fim"]])
        (args.output / f"{path.stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
