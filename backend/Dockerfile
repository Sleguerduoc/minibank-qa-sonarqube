FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 minibank \
    && mkdir -p /srv/data \
    && chown minibank:minibank /srv/data
# Copia explícita: solo el código productivo y los scripts del laboratorio.
COPY app ./app
COPY scripts ./scripts
COPY pytest.ini ./
ENV MINIBANK_DB_PATH=/srv/data/minibank_v3.db
USER minibank
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
