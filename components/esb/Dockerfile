FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -i https://pypi.org/simple -r requirements.txt
COPY app ./app
EXPOSE 7002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7002"]