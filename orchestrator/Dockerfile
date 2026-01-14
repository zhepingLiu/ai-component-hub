FROM repo.pub.cqrcb.com:18083/cqrcb-docker-group/base/kylin-python-arm64:v3.11.14
WORKDIR /app
COPY wheelhouse/ /wheels/
COPY orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt
COPY orchestrator/app ./app
COPY orchestrator/routes.yaml ./routes.yaml

EXPOSE 7010
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "7010"]
