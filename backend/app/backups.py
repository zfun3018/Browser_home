"""HTTP bridge to the isolated backup service."""

from collections.abc import AsyncIterator
import re

import httpx


_JOB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


class BackupServiceError(Exception):
    pass


class BackupCoordinator:
    def __init__(self, base_url: str = "http://backup:8010"):
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def validate_job_id(job_id: str) -> str:
        if not _JOB_ID.fullmatch(job_id):
            raise BackupServiceError("备份任务不存在")
        return job_id

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.request(method, f"{self.base_url}{path}", **kwargs)
            if response.status_code >= 400:
                raise BackupServiceError("备份服务无法完成请求")
            return response.json()
        except BackupServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise BackupServiceError("备份服务暂时不可用") from exc

    async def create_backup(self) -> dict:
        return await self._json("POST", "/internal/backups")

    async def import_status(self, job_id: str) -> dict:
        job_id = self.validate_job_id(job_id)
        return await self._json("GET", f"/internal/imports/{job_id}/status")

    async def prepare_import(self, upload: AsyncIterator[bytes], filename: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{self.base_url}/internal/imports/prepare",
                    content=upload,
                    headers={"x-backup-filename": filename},
                )
            if response.status_code >= 400:
                detail = response.json().get("detail", "备份文件校验失败")
                raise BackupServiceError(str(detail))
            return response.json()
        except BackupServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise BackupServiceError("备份服务暂时不可用") from exc

    async def mark_rollback_delivered(self, job_id: str) -> dict:
        job_id = self.validate_job_id(job_id)
        return await self._json("POST", f"/internal/imports/{job_id}/rollback-delivered")

    async def commit_import(self, job_id: str) -> dict:
        job_id = self.validate_job_id(job_id)
        return await self._json("POST", f"/internal/imports/{job_id}/commit", timeout=600)

    async def cancel_import(self, job_id: str) -> dict:
        job_id = self.validate_job_id(job_id)
        return await self._json("POST", f"/internal/imports/{job_id}/cancel")

    async def stream(self, path: str) -> AsyncIterator[bytes]:
        client = httpx.AsyncClient(timeout=None)
        try:
            request = client.build_request("GET", f"{self.base_url}{path}")
            response = await client.send(request, stream=True)
            if response.status_code >= 400:
                await response.aclose()
                raise BackupServiceError("备份文件无法生成或读取")

            async def chunks():
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                except httpx.HTTPError as exc:
                    raise BackupServiceError("备份传输中断") from exc
                finally:
                    await response.aclose()
                    await client.aclose()

            return chunks()
        except BackupServiceError:
            await client.aclose()
            raise
        except httpx.HTTPError as exc:
            await client.aclose()
            raise BackupServiceError("备份服务暂时不可用") from exc

    async def backup_download(self, job_id: str) -> AsyncIterator[bytes]:
        job_id = self.validate_job_id(job_id)
        return await self.stream(f"/internal/backups/{job_id}/download")

    async def rollback_download(self, job_id: str) -> AsyncIterator[bytes]:
        job_id = self.validate_job_id(job_id)
        return await self.stream(f"/internal/imports/{job_id}/rollback")
