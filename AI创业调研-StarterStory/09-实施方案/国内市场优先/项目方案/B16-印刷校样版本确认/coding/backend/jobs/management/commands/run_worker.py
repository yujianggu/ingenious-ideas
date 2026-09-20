import socket
import time
import uuid
from django.core.management.base import BaseCommand
from jobs.worker import claim_job, run_job, cleanup_attempts


class Command(BaseCommand):
    help = 'Run the leased export worker (expired leases are reclaimed automatically).'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--lease-seconds', type=int, default=120)
    def handle(self, *args, **options):
        worker = f'{socket.gethostname()}-{uuid.uuid4()}'
        next_cleanup = 0
        while True:
            if time.monotonic() >= next_cleanup:
                cleanup_attempts()
                next_cleanup = time.monotonic() + 30
            job = claim_job(worker, options['lease_seconds'])
            if job:
                run_job(job.id, lease_token=job.lease_token)
            if options['once']:
                return
            if job is None:
                time.sleep(1)
