FROM repo.pub.cqrcb.com:18083/cqrcb-docker-group/base/kylin-python-arm64:v3.11.14
WORKDIR /app
COPY wheelhouse/ /wheels/
COPY orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt
COPY orchestrator/app ./app
COPY orchestrator/agents.yaml ./agents.yaml
COPY orchestrator/gunicorn.conf.py ./gunicorn.conf.py

EXPOSE 7010
CMD ["gunicorn", "app.app:app", "-k", "uvicorn.workers.UvicornWorker", "--config", "./gunicorn.conf.py"]
