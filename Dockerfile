FROM python:3.12-slim
WORKDIR /service
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-liberation libreoffice-writer && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY data ./data
COPY scripts ./scripts
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "python -m scripts.initialize_public && python -m scripts.initialize_roles && python -m scripts.remove_generated_examples && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]