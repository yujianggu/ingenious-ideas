from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler

class Conflict(APIException):
    status_code = 409
    default_code = "revision_conflict"
    def __init__(self, message="记录已被更新", current_revision=None):
        super().__init__(message, self.default_code)
        self.current_revision = current_revision

def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ValidationError):
        response.status_code = 422
        code = "validation_error"
        message = "提交字段无效"
    else:
        code = getattr(exc, "default_code", "error")
        detail = getattr(exc, "detail", "请求失败")
        if isinstance(detail, dict):
            message = "请求失败"
        elif isinstance(detail, list):
            message = str(detail[0]) if detail else "请求失败"
        else:
            message = str(detail)
    response.data = {"code": code, "message": message, "current_revision": getattr(exc, "current_revision", None)}
    return response
