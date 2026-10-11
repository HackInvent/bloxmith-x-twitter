"""Read one image strictly inside media_root, before the wallet is read or anything is sent.

The path is resolved component by component from the canonical root with
O_NOFOLLOW, so a symbolic link swapped in after validation cannot redirect the
read. Only regular, single-link files are accepted, and their type is detected
from their first bytes, never from the file name.
"""

import os
from pathlib import Path
import stat

from . import logic


def parts_inside(path_text, config):
    """Return the canonical root and the relative components of the image path."""
    if not config["media_root"]:
        logic.fail("media_root_required")
    configured = Path(os.path.normpath(config["media_root"]))
    try:
        root = configured.resolve(strict=True)
    except (OSError, RuntimeError):
        logic.fail("media_root_required", "media_root does not exist or cannot be read; nothing was sent.")
    if not root.is_dir() or root == Path(root.anchor):
        logic.fail("media_root_required", "media_root must be an existing dedicated directory, not a filesystem root.")
    candidate = Path(path_text)
    if any(part in ("..", ".") for part in candidate.parts):
        logic.fail("media_not_allowed")
    if candidate.is_absolute():
        normalized = Path(os.path.normpath(candidate))
        for base in (configured, root):
            try:
                relative = normalized.relative_to(base)
                break
            except ValueError:
                continue
        else:
            logic.fail("media_not_allowed")
    else:
        relative = candidate
    parts = [part for part in relative.parts if part not in ("", "/")]
    if not parts:
        logic.fail("media_not_allowed")
    return root, parts


def read_image(path_text, config):
    """Return (bytes, media_type) of one JPEG, PNG or WEBP image of at most max_media_bytes."""
    root, parts = parts_inside(path_text, config)
    flags = os.O_RDONLY | os.O_CLOEXEC
    try:
        directory = os.open(root, flags | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        logic.fail("media_not_allowed")
    descriptor = None
    try:
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        logic.fail("media_not_found")
    except OSError:
        logic.fail("media_not_allowed")
    finally:
        os.close(directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            logic.fail("media_not_allowed")
        if info.st_size > config["max_media_bytes"]:
            logic.fail("media_too_large")
        if info.st_size <= 0:
            logic.fail("media_empty")
        chunks, total = [], 0
        while total <= config["max_media_bytes"]:
            piece = os.read(descriptor, min(65536, config["max_media_bytes"] + 1 - total))
            if not piece:
                break
            chunks.append(piece)
            total += len(piece)
    except OSError:
        logic.fail("media_not_allowed")
    finally:
        os.close(descriptor)
    if total > config["max_media_bytes"]:
        logic.fail("media_too_large")
    if total == 0:
        logic.fail("media_empty")
    data = b"".join(chunks)
    return data, logic.image_type(data[:16])
