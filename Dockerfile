FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 60 --retries 2 -r requirements.txt
COPY . .

EXPOSE 5000

CMD ["sh", "-c", "python -c 'from app_postgres import init_db; init_db()' && exec gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 app_postgres:app"]