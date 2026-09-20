from config.exceptions import Conflict


class IdempotencyConflict(Conflict):
    default_code = "idempotency_conflict"

    def __init__(self):
        super().__init__("幂等键已用于不同请求")
