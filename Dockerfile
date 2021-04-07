FROM python:3.9-alpine
RUN mkdir -p /app/
ADD ./* /app/
ADD ./src/celery_redis_prometheus /app/src/celery_redis_prometheus

WORKDIR /app

ENV PYTHONUNBUFFERED 1
RUN pip3 install -r requirements.txt
ENTRYPOINT ["celery"]
CMD []

EXPOSE 9691
