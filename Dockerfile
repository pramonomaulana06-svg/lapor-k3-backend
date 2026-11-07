FROM python:3.10-slim

WORKDIR /app

# Salin requirements dan install dependensi
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin semua kode backend
COPY . .

# Hugging Face Spaces secara default menggunakan port 7860
EXPOSE 7860

# Jalankan FastAPI di port 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]