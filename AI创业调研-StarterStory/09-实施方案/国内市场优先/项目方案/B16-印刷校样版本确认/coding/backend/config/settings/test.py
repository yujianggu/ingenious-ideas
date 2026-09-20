from .base import *

DATABASES = {"default": database_config("b16_proofing_dev")}
DATABASES["default"]["TEST"] = {"NAME": "b16_proofing_test"}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
