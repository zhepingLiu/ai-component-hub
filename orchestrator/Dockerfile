FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -i https://pypi.org/simple -r requirements.txt
COPY app ./app

EXPOSE 7010
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "7010"]