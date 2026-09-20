import hashlib
import re
import tempfile
import unicodedata
import uuid
from urllib.parse import quote

from django.http import StreamingHttpResponse
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.serializers import UUIDField
from rest_framework.views import APIView
from django.conf import settings
from common.idempotency import execute_once
from config.exceptions import Conflict
from proofing.api import active_scope, require_management_write
from proofing.models import Order
from .models import FileRecord
from .storage import get_store
from .validation import validate_record


def member_order(request, owner_id):
    membership = active_scope(request)
    order = Order.objects.filter(pk=owner_id).first()
    if order is None:
        raise NotFound()
    if order.tenant_id != membership.tenant_id:
        raise PermissionDenied()
    return order


def serialize(record):
    return {'id': str(record.id), 'owner_id': str(record.owner_id), 'display_name': record.display_name, 'sha256': record.sha256, 'size': record.size, 'state': record.state, 'failure_reason': record.failure_reason}


def display_name(name):
    value = ''.join('_' if char in '/\\' or unicodedata.category(char).startswith('C') else char for char in str(name)).strip('. ')
    if not value or len(value) > 200:
        raise ValidationError({'file': ['文件名无效或过长']})
    return value


class UploadView(APIView):
    def post(self, request):
        require_management_write(request)
        owner_id = UUIDField().run_validation(request.data.get('owner_id'))
        order = member_order(request, owner_id)
        uploaded = request.FILES.get('file')
        if uploaded is None:
            raise ValidationError({'file': ['请选择文件']})
        name = display_name(uploaded.name)
        digest, size = hashlib.sha256(), 0
        with tempfile.TemporaryFile() as spool:
            for chunk in uploaded.chunks():
                size += len(chunk)
                if size > settings.MAX_UPLOAD_BYTES:
                    raise ValidationError({'file': ['文件超过大小限制']})
                digest.update(chunk)
                spool.write(chunk)
            payload = {key: request.data.getlist(key) for key in request.data if key != 'file'}
            payload.update(owner_id=str(owner_id), sha256=digest.hexdigest(), size=size, display_name=name)
            stored = []
            store = get_store()
            def command():
                key = uuid.uuid4().hex
                spool.seek(0)
                stored.append(key)
                try:
                    store.put(key, spool)
                except FileExistsError:
                    stored.remove(key)  # Never delete a pre-existing immutable object.
                    raise
                record = FileRecord.objects.create(tenant=order.tenant, owner=order, uploaded_by=request.user, object_key=key, display_name=name, sha256=digest.hexdigest(), size=size)
                return serialize(record)
            try:
                result = execute_once((order.tenant_id, request.user.id, 'file.upload', owner_id), request.headers.get('Idempotency-Key'), payload, command)
            except BaseException:
                for key in stored:
                    store.delete(key)
                raise
        return Response(result, status=201)


class ValidateView(APIView):
    def post(self, request, file_id):
        require_management_write(request)
        record = FileRecord.objects.filter(pk=file_id).first()
        if record is None:
            raise NotFound()
        order = member_order(request, record.owner_id)
        payload = dict(request.data)
        payload.update(file_id=str(file_id), owner_id=str(order.id), sha256=record.sha256)
        def command():
            locked = FileRecord.objects.select_for_update().get(pk=file_id, tenant=order.tenant)
            if locked.state == 'quarantined':
                locked.state, locked.failure_reason, locked.content_type = validate_record(locked, get_store())
                locked.save(update_fields=['state', 'failure_reason', 'content_type'])
            return serialize(locked)
        result = execute_once((order.tenant_id, request.user.id, 'file.validate', file_id), request.headers.get('Idempotency-Key'), payload, command)
        return Response(result)


def range_bounds(header, size):
    if header is None:
        return 0, size - 1
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError()
    first, last = match.groups()
    if not first:
        if int(last) <= 0:
            raise ValueError()
        return max(0, size - int(last)), size - 1
    start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError()
    return start, end


def private_headers(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class ContentView(APIView):
    permission_classes = [AllowAny]
    def finalize_response(self, request, response, *args, **kwargs):
        return private_headers(super().finalize_response(request, response, *args, **kwargs))
    def get(self, request, file_id):
        record = FileRecord.objects.filter(pk=file_id).first()
        if record is None:
            raise NotFound()
        if request.user.is_authenticated:
            member_order(request, record.owner_id)
        else:
            if record.purpose == 'export':
                raise PermissionDenied('完整导出仅限店内成员')
            from identity.invitations import require_external_access
            require_external_access(request, record)
        if record.state != 'ready':
            raise Conflict('文件尚未通过校验')
        try:
            start, end = range_bounds(request.headers.get('Range'), record.size)
        except ValueError:
            response = Response(status=416)
            response['Content-Range'] = f'bytes */{record.size}'
            return private_headers(response)
        try:
            source = get_store().open(record.object_key)
        except FileNotFoundError:
            raise NotFound('原文件不可用')
        source.seek(start)
        length = end - start + 1
        def chunks():
            try:
                remaining = length
                while remaining:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                source.close()
        response = StreamingHttpResponse(chunks(), status=206 if request.headers.get('Range') else 200, content_type=record.content_type)
        response['Content-Length'] = length
        response['Accept-Ranges'] = 'bytes'
        response['Content-Disposition'] = "inline; filename*=UTF-8''" + quote(record.display_name, safe='')
        if request.headers.get('Range'):
            response['Content-Range'] = f'bytes {start}-{end}/{record.size}'
        return private_headers(response)
