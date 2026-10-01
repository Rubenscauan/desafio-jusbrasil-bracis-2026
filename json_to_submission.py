# -*- coding: utf-8 -*-
"""Converte JSONs do contrato de saída em um CSV aceito pelo Kaggle."""

import csv
import json
import sys
from pathlib import Path


def encode(documento: dict) -> str:
    partes = []
    for citacao in documento.get("citacoes", []):
        resolucao = citacao.get("resolucao") or {}
        id_canonico = str(resolucao.get("id_canonico", "") or "").strip() or "-"
        confianca = citacao.get("confianca")
        confianca_serializada = "-" if confianca is None else f"{float(confianca):.4f}"
        partes.append(
            f"{int(citacao['inicio'])},{int(citacao['fim'])},"
            f"{citacao['classificacao']},{id_canonico},{confianca_serializada}"
        )
    return "|".join(partes) if partes else "-"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("uso: python json_to_submission.py <pasta_com_jsons> [submission.csv]")

    pasta = Path(sys.argv[1])
    destino = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("submission.csv")
    arquivos = sorted(pasta.glob("*.json"))
    if not arquivos:
        sys.exit(f"nenhum .json encontrado em {pasta}")

    linhas = []
    for arquivo in arquivos:
        documento = json.loads(arquivo.read_text(encoding="utf-8"))
        documento_id = documento.get("documento_id") or arquivo.stem
        linhas.append((documento_id, encode(documento)))

    with destino.open("w", newline="", encoding="utf-8") as arquivo_csv:
        escritor = csv.writer(arquivo_csv)
        escritor.writerow(["documento_id", "citacoes"])
        escritor.writerows(linhas)

    print(f"{destino}: {len(linhas)} documentos.")


if __name__ == "__main__":
    main()
