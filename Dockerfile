FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/main.py src/number_position.py src/semantic_detector.py ./src/
COPY src/pipeline_v2.py src/enhance_baseline.py ./src/
COPY json_to_submission.py run.sh ./
RUN chmod 0555 /app/run.sh

ENTRYPOINT ["/app/run.sh"]
