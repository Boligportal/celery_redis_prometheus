from setuptools import setup, find_packages


setup(
    name='celery_redis_prometheus',
    version='2.0.0.dev0',
    author='BoligPortal ApS',
    author_email='dev@boligportal.dk',
    url='https://github.com/Boligportal/celery_redis_prometheus',
    description="Exports task execution metrics in Prometheus format",
    long_description='\n\n'.join(
        open(x).read() for x in ['README.rst', 'CHANGES.txt']),
    packages=find_packages('src'),
    package_dir={'': 'src'},
    include_package_data=True,
    zip_safe=False,
    license='BSD',
    install_requires=[
        'celery',
        'prometheus_client',
        'setuptools',
    ],
    extras_require={'test': [
        'pytest',
    ]},
    entry_points={
        'celery.commands': [
            'prometheus = celery_redis_prometheus.exporter:prometheus',
        ]
    }
)
