"""Delivery adapters: development capture is never usable with production settings."""
import json
from urllib.error import URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException


class DeliveryUnavailable(APIException):
    status_code = 503
    default_code = 'code_delivery_unavailable'
    default_detail = '接收人验证码服务暂不可用'


class DevelopmentCodeProvider:
    messages = []
    def __init__(self):
        if settings.DEPLOYMENT_ENV == 'production':
            raise ImproperlyConfigured('Development code provider forbidden in production')

    def send_code(self, receiver, code, request_id):
        self.messages.append({'receiver': receiver, 'code': code, 'request_id': request_id})


class NoDeliveryRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class HTTPCodeProvider:
    """Configurable HTTPS delivery bridge. Response must explicitly acknowledge acceptance."""
    def send_code(self, receiver, code, request_id):
        request = Request(settings.CODE_PROVIDER_ENDPOINT, data=json.dumps({'receiver': receiver, 'code': code, 'request_id': request_id}).encode(), headers={'Authorization': 'Bearer ' + settings.CODE_PROVIDER_API_KEY, 'Content-Type': 'application/json', 'Idempotency-Key': request_id}, method='POST')
        try:
            with build_opener(NoDeliveryRedirects()).open(request, timeout=10) as response:
                if response.status != 200 or json.loads(response.read(4096)).get('accepted') is not True:
                    raise DeliveryUnavailable()
        except (URLError, OSError, ValueError):
            raise DeliveryUnavailable()


def get_code_provider():
    if not settings.CODE_PROVIDER:
        raise DeliveryUnavailable()
    return import_string(settings.CODE_PROVIDER)()
