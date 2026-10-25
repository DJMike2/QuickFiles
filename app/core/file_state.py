"""File identity snapshots and cancellable streaming verification."""
import hashlib


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def file_hash(path, cancelled=None):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            if cancelled and cancelled():
                raise InterruptedError('File verification stopped')
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
