FROM repo.pub.cqrcb.com:18083/cqrcb-docker-group/base/kylin-python-arm64:v3.11.14
WORKDIR /app
COPY wheelhouse/ /wheels/
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt
COPY app ./app
COPY routes.yaml ./routes.yaml
COPY gunicorn.conf.py ./gunicorn.conf.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--config", "./gunicorn.conf.py"]