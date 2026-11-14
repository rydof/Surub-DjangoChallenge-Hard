from django.core.management.base import BaseCommand

from simulator.tasks import download_people_task


class Command(BaseCommand):
    help = 'Enqueue (or run) the Celery task to download people from the API.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously in-process instead of queuing to a Celery worker.',
        )

    def handle(self, *args, **options):
        if options['sync']:
            result = download_people_task()
            self.stdout.write(self.style.SUCCESS(f'Downloaded people synchronously: {result}'))
        else:
            task = download_people_task.delay()
            self.stdout.write(self.style.SUCCESS(f'Queued download_people_task: {task.id}'))
