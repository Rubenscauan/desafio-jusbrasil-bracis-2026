#!/usr/bin/env bash
# Uso: run.sh <caminho_db> <pasta_txt> <arquivo_csv_saida>
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "uso: run.sh <caminho_db> <pasta_txt> <arquivo_csv_saida>" >&2
  exit 2
fi

database="$1"
input_dir="$2"
output_csv="$3"

[[ -f "$database" ]] || { echo "banco inexistente: $database" >&2; exit 2; }
[[ -d "$input_dir" ]] || { echo "pasta inexistente: $input_dir" >&2; exit 2; }

mkdir -p "$(dirname "$output_csv")"
temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT

python /app/src/enhance_baseline.py \
  --database "$database" \
  --input "$input_dir" \
  --output "$temporary_dir/json"
python /app/json_to_submission.py "$temporary_dir/json" "$output_csv"
