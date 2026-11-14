from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand

from simulator.models import Person
from simulator.tasks import (
    draft_batch_task,
    draft_people_batch_task,
    enqueue_drafting_batches,
    enqueue_remote_draft_batches,
)


class Command(BaseCommand):
    help = 'Enqueue (or run) separate Celery batch tasks to draft registered people.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=10)
        parser.add_argument('--batches', type=int, default=10)
        parser.add_argument(
            '--remote',
            action='store_true',
            help='Fetch drafted batches from remote /draft/<batch_index> API endpoints.',
        )
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run batches synchronously in-process instead of queuing to a Celery worker.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        num_batches = options['batches']
        if options['remote']:
            if options['sync']:
                with ThreadPoolExecutor(max_workers=num_batches) as pool:
                    results = list(pool.map(draft_batch_task, range(num_batches)))
                self.stdout.write(
                    self.style.SUCCESS(f'Drafted {len(results)} remote batches in parallel')
                )
            else:
                task_ids = enqueue_remote_draft_batches(num_batches=num_batches)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Queued {len(task_ids)} remote drafting batch tasks: {", ".join(task_ids)}'
                    )
                )
            return

        if options['sync']:
            undrafted_ids = list(
                Person.objects.filter(rank='').order_by('external_id').values_list('id', flat=True)
            )
            results = []
            for start in range(0, len(undrafted_ids), batch_size):
                batch = undrafted_ids[start : start + batch_size]
                results.append(draft_people_batch_task(batch))
            self.stdout.write(
                self.style.SUCCESS(f'Drafted {len(results)} batches synchronously: {results}')
            )
        else:
            task_ids = enqueue_drafting_batches(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Queued {len(task_ids)} drafting batch tasks: {", ".join(task_ids)}'
                )
            )
