"""Private, immutable object stores. No public or presigned download URL API."""
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class PrivateStore(Protocol):
    def put(self, key: str, stream: BinaryIO) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...


def safe_key(key):
    if not re.fullmatch(r'[0-9a-f]{32}', key):
        raise ValueError('Invalid object key')
    return key


class LocalPrivateStore:
    """Development only. UUID names, exclusive writes and owner-only permissions."""
    def __init__(self, root):
        if settings.DEPLOYMENT_ENV == 'production':
            raise ImproperlyConfigured('LocalPrivateStore is forbidden in production')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def put(self, key, stream):
        path = self.root / safe_key(key)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as target:
                shutil.copyfileobj(stream, target, 64 * 1024)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def open(self, key):
        fd = os.open(self.root / safe_key(key), os.O_RDONLY | os.O_NOFOLLOW)
        return os.fdopen(fd, 'rb')

    def delete(self, key):
        (self.root / safe_key(key)).unlink(missing_ok=True)


class OssPrivateStore:
    def __init__(self):
        import oss2
        from oss2.credentials import EnvironmentVariableCredentialsProvider
        self.bucket = oss2.Bucket(oss2.ProviderAuthV4(EnvironmentVariableCredentialsProvider()), settings.OSS_ENDPOINT, settings.OSS_BUCKET, region=settings.OSS_REGION, connect_timeout=10)
        if self.bucket.get_bucket_acl().acl != oss2.BUCKET_ACL_PRIVATE:
            raise ImproperlyConfigured('OSS bucket must be private')

    def put(self, key, stream):
        import oss2
        try:
            self.bucket.put_object(safe_key(key), stream, headers={'x-oss-object-acl': 'private', 'x-oss-forbid-overwrite': 'true'})
        except oss2.exceptions.ObjectAlreadyExists as exc:
            raise FileExistsError(key) from exc

    def open(self, key):
        import oss2
        try:
            source = self.bucket.get_object(safe_key(key))
        except oss2.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(key) from exc
        spool = tempfile.TemporaryFile()
        try:
            total = 0
            while chunk := source.read(64 * 1024):
                total += len(chunk)
                if total > settings.MAX_UPLOAD_BYTES:
                    raise ValueError('Stored object exceeds maximum size')
                spool.write(chunk)
            spool.seek(0)
            return spool
        except BaseException:
            spool.close()
            raise
        finally:
            source.close()

    def delete(self, key):
        self.bucket.delete_object(safe_key(key))


def get_store():
    if settings.PRIVATE_STORE == 'local':
        return LocalPrivateStore(settings.PRIVATE_LOCAL_ROOT)
    if settings.PRIVATE_STORE == 'oss':
        return OssPrivateStore()
    raise ImproperlyConfigured('Private object store is not configured')
