"""Avalia uma submissão local com o scorer oficial incluído no ZIP do desafio.

Ferramenta apenas de desenvolvimento: pandas/numpy são dependências do scorer;
este módulo não é importado pelo pipeline de execução final.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path

import pandas as pd


def solution_from_offsets(path: Path) -> pd.DataFrame:
    grouped: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            doc_id = row["documento_id"]
            item = grouped.setdefault(doc_id, {"nivel": int(row["nivel"]), "citacoes": []})
            canonical_id = (row.get("id_canonico") or "-").strip() or "-"
            item["citacoes"].append(
                f"{int(row['inicio'])},{int(row['fim'])},{row['classificacao'].strip()}#{canonical_id}"
            )

    rows = []
    for doc_id, item in grouped.items():
        citations = []
        for packed in item["citacoes"]:
            span, canonical_id = packed.split("#", 1)
            citations.append(f"{span},{canonical_id}")
        rows.append({"documento_id": doc_id, "nivel": item["nivel"], "citacoes": "|".join(citations)})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True, help="goldenset_offsets.csv do ZIP")
    parser.add_argument("--submission", type=Path, required=True, help="CSV no formato Kaggle")
    parser.add_argument("--metric", type=Path, required=True, help="kaggle_metric.py oficial do ZIP")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("official_kaggle_metric", args.metric)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Não foi possível carregar o scorer: {args.metric}")
    metric = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metric)
    solution = solution_from_offsets(args.gold)
    submission = pd.read_csv(args.submission, dtype={"documento_id": str}, keep_default_na=False)
    report = metric.avaliar(solution, submission)
    print(f"score_final={report['score_final']:.8f}")
    for level, values in report["niveis"].items():
        print(
            f"N{level}: score={values['score']:.8f} macro_f1={values['macro_f1']:.8f} "
            f"tau={values['tau']:.6f} bonus={values['b']:.6f}"
        )


if __name__ == "__main__":
    main()
