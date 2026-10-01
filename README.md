# Caça-Alucinações — BRACIS 2026

Pipeline determinístico para localizar citações jurídicas, classificá-las como
`real`, `inventada` ou `incompleta` e resolver referências reais contra a base
SQLite fornecida pelo desafio.

## Execução

O ponto de entrada da solução recebe o banco, a pasta com os documentos `.txt`
e o caminho do CSV de saída:

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida.csv>
```

Exemplo com Docker:

```bash
docker build -t caca-alucinacoes .
docker run --rm \
  -v "$(pwd)/dados:/input:ro" \
  -v "$(pwd)/resultado:/output" \
  caca-alucinacoes \
  /input/desafio1_bracis.db /input/txt /output/submission.csv
```

O CSV usa as colunas `documento_id,citacoes`, compatíveis com o conversor do
desafio. A imagem é baseada em Python 3.12, não depende de rede durante a
execução e não requer GPU.

## Abordagem

- Detecta referências a processos, súmulas, dispositivos legais e citações
  jurisprudenciais incompletas com padrões explícitos.
- Normaliza acentos, abreviações e confusões OCR numéricas comuns.
- Resolve processos no cabeçalho dos acórdãos para evitar confundir uma decisão
  citada no corpo com o próprio acórdão.
- Usa uma busca complementar fora do cabeçalho apenas para identificadores
  declarados como processo principal e únicos no acervo.
- Gera confiança determinística e ordena as citações por posição no texto.

## Estrutura

- `run.sh`: entrada única usada pela execução final.
- `src/enhance_baseline.py`: orquestra detecção, resolução e saída JSON interna.
- `src/pipeline_v2.py`: regras de citação e índice canônico por cabeçalho.
- `src/main.py`, `src/number_position.py`, `src/semantic_detector.py`:
  componentes do detector-base.
- `json_to_submission.py`: converte os JSONs internos ao CSV de submissão.
- `src/evaluate_official_zip.py`: utilitário de desenvolvimento para comparar
  uma saída ao scorer v0.2 e ao gold incluídos no ZIP do desafio.

## Validação

No conjunto de desenvolvimento do ZIP v0.2 analisado, a versão atual obteve
**1,09853** na métrica oficial, com F1 macro 1,00 em N1 e N2. Essa medição usa
o gold disponível para desenvolvimento; não representa nem garante a nota no
conjunto oculto da avaliação final.

O avaliador auxiliar requer `pandas` e `numpy` além das dependências da solução.
Mantenha `goldenset_offsets.csv` e `kaggle_metric.py` fora deste repositório;
indique seus caminhos localmente:

```powershell
python .\src\evaluate_official_zip.py `
  --gold <pasta_do_zip>\goldenset_offsets.csv `
  --metric <pasta_do_zip>\kaggle_metric.py `
  --submission <arquivo_saida.csv>
```

## Ambiente

As dependências da execução estão fixadas em `requirements.txt`. O banco,
documentos, gold, modelos locais, resultados intermediários e CSVs são dados de
desenvolvimento e não fazem parte do contexto Docker nem do repositório Git.
