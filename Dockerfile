FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY trilha trilha
COPY migrations migrations
COPY alembic.ini .
USER app
EXPOSE 8000
CMD ["uvicorn", "trilha.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
