FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storage.py app.py ./
ENV NOTES_DATA_DIR=/data
EXPOSE 8080
CMD ["python", "app.py"]
