from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile


def make_zip(files: dict[str, str]) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buf.getvalue()


def files_dict(pkg) -> dict[str, str]:
    return {file.path: file.content for file in pkg.files}
