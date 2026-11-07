FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
# One process per replica on purpose: its 40 worker threads are a resource the labs exhaust.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
