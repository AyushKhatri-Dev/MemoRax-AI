from django.apps import AppConfig


class MemoryEngineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'memory_engine'
    verbose_name = 'Memory Engine'

    def ready(self):
        import os
        import sys

        # Skip during migrations / management commands that don't serve requests
        skip_commands = {'migrate', 'makemigrations', 'collectstatic', 'test',
                         'shell', 'createsuperuser', 'dbshell', 'check', 'showmigrations'}
        if any(cmd in sys.argv for cmd in skip_commands):
            return

        # In dev with autoreload: RUN_MAIN=true only in the child (server) process.
        # In prod (gunicorn / --noreload): RUN_MAIN is unset — start scheduler anyway.
        run_main = os.environ.get('RUN_MAIN')
        if run_main == 'true' or run_main is None:
            try:
                from memory_engine.scheduler import start_scheduler
                start_scheduler()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Scheduler start failed: {e}")
