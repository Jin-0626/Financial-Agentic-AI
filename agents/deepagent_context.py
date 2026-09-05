from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    create_file_data,
    file_data_to_string,
    grep_matches_from_files,
    perform_string_replacement,
    slice_read_response,
)

from agents.config import default_config

MEMORY_PATHS = ["/memories/AGENTS.md"]
SKILL_PATHS = ["/skills/"]


class BursaMemorySkillBackend:
    """Narrow backend exposing only DeepAgents memory and skill files."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir or default_config["project_dir"]).resolve()

    def _validate_virtual_path(self, path: str) -> PurePosixPath:
        if not path.startswith("/"):
            path = "/" + path
        virtual_path = PurePosixPath(path)
        if ".." in virtual_path.parts or "~" in virtual_path.parts:
            raise ValueError("Path traversal is not allowed")
        return virtual_path

    def _is_readable(self, path: PurePosixPath) -> bool:
        path_text = path.as_posix()
        return path_text == "/memories/AGENTS.md" or path_text.startswith("/skills/")

    def _is_writable(self, path: PurePosixPath) -> bool:
        return path.as_posix() == "/memories/AGENTS.md"

    def _to_real_path(self, path: PurePosixPath) -> Path:
        resolved = (self.root_dir / path.as_posix().lstrip("/")).resolve()
        try:
            resolved.relative_to(self.root_dir)
        except ValueError:
            raise ValueError("Resolved path escaped the project root") from None
        return resolved

    def _file_data_map(self) -> dict[str, dict]:
        files: dict[str, dict] = {}
        for virtual_path in [PurePosixPath("/memories/AGENTS.md"), *self._skill_files()]:
            real_path = self._to_real_path(virtual_path)
            if real_path.is_file():
                files[virtual_path.as_posix()] = create_file_data(
                    real_path.read_text(encoding="utf-8")
                )
        return files

    def _skill_files(self) -> list[PurePosixPath]:
        skills_root = self.root_dir / "skills"
        if not skills_root.is_dir():
            return []
        paths = []
        for real_path in sorted(skills_root.rglob("*")):
            if real_path.is_file():
                paths.append(
                    PurePosixPath("/")
                    / real_path.resolve().relative_to(self.root_dir).as_posix()
                )
        return paths

    def ls(self, path: str) -> LsResult:
        try:
            virtual_path = self._validate_virtual_path(path)
        except ValueError as exc:
            return LsResult(error=str(exc), entries=None)
        if not self._is_readable(virtual_path) and virtual_path.as_posix() not in {
            "/",
            "/memories",
            "/skills",
        }:
            return LsResult(error=f"Path '{path}': permission_denied", entries=None)

        prefix = virtual_path.as_posix().rstrip("/") + "/"
        entries: list[FileInfo] = []
        subdirs: set[str] = set()
        for file_path, file_data in self._file_data_map().items():
            if file_path == virtual_path.as_posix():
                entries.append(
                    FileInfo(
                        path=file_path,
                        is_dir=False,
                        size=len(file_data_to_string(file_data)),
                        modified_at=file_data.get("modified_at", ""),
                    )
                )
                continue
            if not file_path.startswith(prefix):
                continue
            rest = file_path[len(prefix) :]
            if "/" in rest:
                subdirs.add(prefix + rest.split("/", 1)[0] + "/")
            else:
                entries.append(
                    FileInfo(
                        path=file_path,
                        is_dir=False,
                        size=len(file_data_to_string(file_data)),
                        modified_at=file_data.get("modified_at", ""),
                    )
                )
        entries.extend(
            FileInfo(path=subdir, is_dir=True, size=0, modified_at="")
            for subdir in sorted(subdirs)
        )
        return LsResult(entries=sorted(entries, key=lambda item: item["path"]))

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            virtual_path = self._validate_virtual_path(file_path)
        except ValueError as exc:
            return ReadResult(error=str(exc))
        if not self._is_readable(virtual_path):
            return ReadResult(error=f"Error: permission denied for read on {file_path}")

        file_data = self._file_data_map().get(virtual_path.as_posix())
        if file_data is None:
            return ReadResult(error=f"File '{file_path}' not found")
        return slice_read_response(file_data, offset, limit)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            virtual_path = self._validate_virtual_path(file_path)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        if not self._is_writable(virtual_path):
            return WriteResult(error=f"Error: permission denied for write on {file_path}")
        real_path = self._to_real_path(virtual_path)
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(content, encoding="utf-8")
        return WriteResult(path=virtual_path.as_posix())

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        current = self.read(file_path)
        if current.error:
            return EditResult(error=current.error)
        result = perform_string_replacement(
            file_data_to_string(current.file_data),
            old_string,
            new_string,
            replace_all,
        )
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        write_result = self.write(file_path, new_content)
        if write_result.error:
            return EditResult(error=write_result.error)
        return EditResult(path=file_path, occurrences=occurrences)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.to_thread(
            self.edit, file_path, old_string, new_string, replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error="Delete is disabled for Bursa memory and skills")

    async def adelete(self, file_path: str) -> DeleteResult:
        return await asyncio.to_thread(self.delete, file_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return grep_matches_from_files(
            self._file_data_map(),
            pattern,
            path if path is not None else "/",
            glob,
            max_count=max_count,
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(
            self.grep, pattern, path, glob, max_count=max_count
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        matches = []
        base = path.rstrip("/") if path else ""
        for file_path, file_data in self._file_data_map().items():
            if base and not file_path.startswith(base + "/") and file_path != base:
                continue
            if PurePosixPath(file_path).match(pattern.lstrip("/")):
                matches.append(
                    FileInfo(
                        path=file_path,
                        is_dir=False,
                        size=len(file_data_to_string(file_data)),
                        modified_at=file_data.get("modified_at", ""),
                    )
                )
        return GlobResult(matches=matches)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=path, error="Upload is disabled")
            for path, _ in files
        ]

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            result = self.read(path)
            if result.error:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=result.error)
                )
            else:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=file_data_to_string(result.file_data).encode("utf-8"),
                        error=None,
                    )
                )
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await asyncio.to_thread(self.download_files, paths)


def build_workspace_backend() -> BursaMemorySkillBackend:
    return BursaMemorySkillBackend()
