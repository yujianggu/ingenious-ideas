from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.serializers import UUIDField
from rest_framework.views import APIView
from common.idempotency import execute_once, request_digest
from files.api import member_order, private_headers
from proofing.api import require_management_write
from proofing.models import Order
from jobs.models import Job
from .service import build_manifest


class ExportView(APIView):
    def post(self, request):
        require_management_write(request)
        owner_id = UUIDField().run_validation(request.data.get('owner_id'))
        order = member_order(request, owner_id)
        def command():
            # Same row lock as publish/decide/revoke fixes a consistent boundary.
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.id, tenant=order.tenant)
                snapshot = build_manifest(order.id, order.tenant_id)
                job = Job.objects.create(tenant=order.tenant, owner=order, requested_by=request.user,
                    snapshot=snapshot, snapshot_sha256=request_digest(snapshot), available_at=timezone.now())
                return {'job_id': str(job.id), 'state': job.state}
        result = execute_once((order.tenant_id, request.user.id, 'export.create', owner_id), request.headers.get('Idempotency-Key'), dict(request.data), command)
        return Response(result, status=202)


class ExportDetailView(APIView):
    def get(self, request, job_id):
        job = Job.objects.filter(pk=job_id).first()
        if job is None:
            raise NotFound()
        member_order(request, job.owner_id)
        return private_headers(Response({'job_id': str(job.id), 'state': job.state,
            'file_id': str(job.file_id) if job.file_id else None, 'error_code': job.error_code or None}))
