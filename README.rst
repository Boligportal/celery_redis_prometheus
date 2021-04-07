=======================
celery_redis_prometheus
=======================

Exports task execution metrics in Prometheus format: how many tasks were started
and have completed successfully or with failure, and how many tasks are still in
the queues (supported only for broker redis).

Inspired by https://gitlab.com/kalibrr/celery-prometheus


Usage
=====

Start HTTP service
------------------

Start the HTTP server like this::

  $ celery prometheus --host=127.0.0.1 --port=9691



Configure Prometheus
--------------------

::

    scrape_configs:
      - job_name: 'celery'
        static_configs:
          - targets: ['localhost:9691']


We export the following metrics:

* ``celery_tasks_total{state="started|succeeded|failed|retried", queue="celery", name="task_name"}``, counter
* ``celery_task_queue_time_seconds``, histogram (only if ``task_send_sent_event`` is enabled in Celery)
* ``celery_task_runtime_seconds``, histogram
* ``celery_task_prefetch_latency_seconds``, histogram
* ``celery_workers``, gauge

If you pass ``--queuelength-interval=x`` then every x seconds the queue lengths will be checked (NOTE: this only works with redis as the broker), resulting in this additional metric:

* ``celery_queue_length{queue="..."}``, gauge

