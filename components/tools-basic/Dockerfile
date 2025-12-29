FROM repo.pub.cqrcb.com:18083/cqrcb-docker-group/base/kylin-python-arm64:v3.11.14
WORKDIR /app
COPY wheelhouse/ /wheels/
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt
COPY app ./app
EXPOSE 7001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7001"]
