import hashlib
import mimetypes
import os
import secrets
import shutil
import stat
from contextlib import ExitStack
from pathlib import Path

from .errors import ProductionError


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_artifact(project_dir, source_path, media_type=None):
    source = Path(source_path)
    if source.is_symlink():
        raise ProductionError("拒绝导入符号链接", "unsafe_artifact")
    source = source.resolve()
    if not source.is_file():
        raise ProductionError("候选文件不存在或不是普通文件", "invalid_artifact")
    content_hash = sha256_file(source)
    relative = Path("media") / "objects" / content_hash[:2] / content_hash
    _write_content_addressed_object(project_dir, source, content_hash)
    detected_type = media_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return {
        "content_hash": content_hash,
        "object_path": relative.as_posix(),
        "media_type": detected_type,
    }


def _write_content_addressed_object(project_dir, source, content_hash):
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        with ExitStack() as stack:
            project_fd = os.open(Path(project_dir), directory_flags)
            stack.callback(os.close, project_fd)
            media_fd = os.open("media", directory_flags, dir_fd=project_fd)
            stack.callback(os.close, media_fd)
            objects_fd = os.open("objects", directory_flags, dir_fd=media_fd)
            stack.callback(os.close, objects_fd)

            prefix = content_hash[:2]
            try:
                os.mkdir(prefix, mode=0o755, dir_fd=objects_fd)
            except FileExistsError:
                pass
            prefix_fd = os.open(prefix, directory_flags, dir_fd=objects_fd)
            stack.callback(os.close, prefix_fd)

            temporary_name = f".{content_hash}.{secrets.token_hex(8)}.tmp"
            temporary_created = False
            try:
                temporary_fd = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | file_no_follow,
                    0o600,
                    dir_fd=prefix_fd,
                )
                temporary_created = True
                with os.fdopen(temporary_fd, "w+b") as target, source.open("rb") as source_handle:
                    shutil.copyfileobj(source_handle, target, length=1024 * 1024)
                    target.flush()
                    target.seek(0)
                    if _sha256_handle(target) != content_hash:
                        raise ProductionError("导入期间候选文件发生变化", "artifact_changed")
                try:
                    os.link(
                        temporary_name,
                        content_hash,
                        src_dir_fd=prefix_fd,
                        dst_dir_fd=prefix_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    _validate_existing_object(prefix_fd, content_hash, file_no_follow)
            finally:
                if temporary_created:
                    try:
                        os.unlink(temporary_name, dir_fd=prefix_fd)
                    except FileNotFoundError:
                        pass
    except ProductionError:
        raise
    except OSError as exc:
        raise ProductionError("素材库路径不安全或不可写", "unsafe_object_store") from exc


def _sha256_handle(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_existing_object(prefix_fd, content_hash, file_no_follow):
    existing_fd = os.open(content_hash, os.O_RDONLY | file_no_follow, dir_fd=prefix_fd)
    with os.fdopen(existing_fd, "rb") as existing:
        if not stat.S_ISREG(os.fstat(existing.fileno()).st_mode):
            raise ProductionError("素材库对象不是普通文件", "unsafe_object_store")
        if _sha256_handle(existing) != content_hash:
            raise ProductionError("素材库对象哈希不匹配", "corrupt_object_store")
