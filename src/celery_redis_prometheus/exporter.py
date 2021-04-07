# Usage:  celery prometheus --help

import collections
import logging
import os
import sys
import threading
import time
from functools import wraps
from typing import Sequence

import click
import prometheus_client
from celery import Celery
from prometheus_client.utils import INF

try:
    import _thread
except ImportError:  # py2
    import thread as _thread

# Log to stdout
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


log = logging.getLogger(__name__)


# Remove any `process_` and `python_` metrics, since we're proxying for the
# whole celery machinery, but those would only be about this process.
for collector in list(prometheus_client.REGISTRY._collector_to_names):
    prometheus_client.REGISTRY.unregister(collector)

DEFAULT_BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60, 60 * 2, 60 * 5, 60 * 10, 60 * 30, INF)


def get_buckets_setting(name: str, default: Sequence[float]) -> Sequence[float]:
    buckets_str = os.getenv(name, None)
    if buckets_str:
        return tuple(float(bucket) for bucket in buckets_str.split(","))
    else:
        return default


QUEUE_TIME_BUCKETS = get_buckets_setting("QUEUE_TIME_BUCKETS", DEFAULT_BUCKETS)
RUNTIME_BUCKETS = get_buckets_setting("RUNTIME_BUCKETS", DEFAULT_BUCKETS)
PREFETCH_LATENCY_BUCKETS = get_buckets_setting("PREFETCH_LATENCY_BUCKETS", DEFAULT_BUCKETS)


STATS = {
    'tasks': prometheus_client.Counter(
        'celery_tasks_total', 'Number of tasks', ['state', 'queue', 'name']),
    'queue_time': prometheus_client.Histogram(
        'celery_task_queue_time_seconds', 'Time between enqueuing the task and starting the task', labelnames=['queue', 'name'], buckets=QUEUE_TIME_BUCKETS),
    'runtime': prometheus_client.Histogram(
        'celery_task_runtime_seconds', 'Task runtime', labelnames=['queue', 'name'], buckets=RUNTIME_BUCKETS),
    'prefetch_latency': prometheus_client.Histogram(
        'celery_task_prefetch_latency_seconds', 'Time spent between prefetching and starting the task', labelnames=['queue', 'name'], buckets=PREFETCH_LATENCY_BUCKETS),
    'queues': prometheus_client.Gauge(
        'celery_queue_length', 'Queue length', ['queue']),
    'workers': prometheus_client.Gauge(
        'celery_workers', 'Number of workers responding to ping')
}


@click.command()
@click.option('--port', default=9691, type=int, help='Server port')
@click.option('--host', default='', help='Server host')
@click.option('--broker', default='', help='Broker URL')
@click.option('--debug', is_flag=True)
@click.option('--queuelength-interval', '-i',
    help='Check queue lengths every x seconds (0=disabled)',
    type=int, default=0)
@click.option('--queuelength-queues', '-q',
    help='Names of queues to monitor length of',
    multiple=True, default=['celery'])
def prometheus(broker, port, host, debug, queuelength_interval, queuelength_queues):
    app = Celery(broker=broker)
    cmd = PrometheusExporterCommand(app=app)
    cmd.prepare_args(port=port, host=host, debug=debug, queuelength_interval=queuelength_interval,  queuelength_queues=queuelength_queues)
    cmd.run()


class PrometheusExporterCommand:
    queuelength_thread = None
    worker_thread = None

    def __init__(self, app):
        self.app = app

    def run(self):
        receiver = CeleryEventReceiver(self.app)

        try_interval = 1
        while True:
            try:
                try_interval *= 2
                receiver()
                try_interval = 1
            except (KeyboardInterrupt, SystemExit):
                log.info('Exiting')
                if self.queuelength_thread:
                    self.queuelength_thread.stop()
                if self.worker_thread:
                    self.worker_thread.stop()
                _thread.interrupt_main()
                break
            except Exception as e:
                log.error(
                    'Failed to capture events: "%s", '
                    'trying again in %s seconds.',
                    e, try_interval, exc_info=True)
                time.sleep(try_interval)

    def prepare_args(self, *, port, host, debug, queuelength_interval,  queuelength_queues):
        self.app.log.setup(
            logging.DEBUG if debug else logging.INFO)

        log.info('Listening on %s:%s',
                 host or '0.0.0.0', port)
        prometheus_client.start_http_server(port, host)

        if queuelength_interval:
            self.queuelength_thread = QueueLengthMonitor(
                self.app, queuelength_interval,  queuelength_queues)
            self.queuelength_thread.start()
        self.worker_thread = WorkerMonitor(self.app, interval=5, timeout=5)
        self.worker_thread.start()


def task_handler(fn):
    @wraps(fn)
    def wrapper(self, event):
        self.state.event(event)
        task = self.state.tasks.get(event['uuid'])
        return fn(self, event, task)
    return wrapper


class CeleryEventReceiver(object):

    def __init__(self, app):
        self.app = app

    @task_handler
    def on_task_started(self, event, task):
        # XXX We'd like to maybe differentiate this by queue, but
        # task.routing_key is always None, even though in redis it contains the
        # queue name.
        log.debug('Started %s', task)
        STATS['tasks'].labels(state='started', queue=task.routing_key, name=task.name).inc()
        if task.sent:
            STATS['queue_time'].labels(queue=task.routing_key, name=task.name).observe(task.started - task.sent)
        if task.received:
            STATS['prefetch_latency'].labels(queue=task.routing_key, name=task.name).observe(task.started - task.received)

    @task_handler
    def on_task_succeeded(self, event, task):
        log.debug('Succeeded %s', task)
        STATS['tasks'].labels(state='succeeded', queue=task.routing_key, name=task.name).inc()
        self.record_runtime(task)

    def record_runtime(self, task):
        if task is not None and task.runtime is not None:
            STATS['runtime'].labels(queue=task.routing_key, name=task.name).observe(task.runtime)

    @task_handler
    def on_task_failed(self, event, task):
        log.debug('Failed %s', task)
        STATS['tasks'].labels(state='failed', queue=task.routing_key, name=task.name).inc()
        self.record_runtime(task)

    @task_handler
    def on_task_retried(self, event, task):
        log.debug('Retried %s', task)
        STATS['tasks'].labels(state='retried', queue=task.routing_key, name=task.name).inc()
        self.record_runtime(task)

    @task_handler
    def on_task_revoked(self, event, task):
        log.debug('Revoked %s', task)
        STATS['tasks'].labels(state='revoked', queue=task.routing_key, name=task.name).inc()

    def __call__(self, *args, **kw):
        self.state = self.app.events.State()
        kw.setdefault('wakeup', False)

        with self.app.connection() as connection:
            recv = self.app.events.Receiver(connection, handlers={
                'task-started': self.on_task_started,
                'task-succeeded': self.on_task_succeeded,
                'task-failed': self.on_task_failed,
                'task-retried': self.on_task_retried,
                'task-revoked': self.on_task_revoked,
                '*': self.state.event,
            })
            recv.capture(*args, **kw)


class QueueLengthMonitor(threading.Thread):

    def __init__(self, app, interval, queues):
        super(QueueLengthMonitor, self).__init__()
        self.app = app
        self.queues = queues
        self.interval = interval
        self.running = True

    def run(self):
        log.info(f'Monitoring queue length for queues: {", ".join(self.queues)}')
        while self.running:
            try:
                lengths = collections.Counter()

                with self.app.connection() as connection:
                    pipe = connection.channel().client.pipeline(
                        transaction=False)
                    for queue in self.queues:
                        # Not claimed by any worker yet
                        pipe.llen(queue)

                    result = pipe.execute()

                for llen, queue in zip(result, self.queues):
                    lengths[queue] += llen

                for queue, length in lengths.items():
                    STATS['queues'].labels(queue).set(length)

                time.sleep(self.interval)
            except Exception:
                log.error(
                    'Uncaught exception, preventing thread from crashing.',
                    exc_info=True)

    def stop(self):
        self.running = False


class WorkerMonitor(threading.Thread):

    def __init__(self, app, *, interval, timeout):
        super(WorkerMonitor, self).__init__()
        self.app = app
        self.interval = interval
        self.timeout = timeout
        self.running = True

    def run(self):
        log.info(f'Monitoring workers')
        while self.running:
            try:
                STATS['workers'].set(len(self.app.control.ping(timeout=self.timeout)))
                time.sleep(self.interval)
            except Exception:
                log.error(
                    'Uncaught exception, preventing thread from crashing.',
                    exc_info=True)

    def stop(self):
        self.running = False
