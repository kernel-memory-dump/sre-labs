FROM python:3.12-slim
RUN pip install --no-cache-dir "locust~=2.31"
WORKDIR /load
COPY locustfile.py .
# Run the selected SHAPE once, then idle, so Container Apps does not restart the test in a loop.
CMD ["sh", "-c", "locust -f locustfile.py --headless --only-summary -H \"$TARGET\"; sleep infinity"]
