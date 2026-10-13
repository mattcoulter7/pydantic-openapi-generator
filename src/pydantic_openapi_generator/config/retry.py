from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from httpx_retries import Retry, RetryTransport


class RetryFactory(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    total: int = Field(default=3, ge=0)
    allowed_methods: list[str] | None = None
    status_forcelist: list[int] | None = None
    backoff_factor: float = Field(default=0.5, ge=0)
    respect_retry_after_header: bool = True
    max_backoff_wait: float = Field(default=120.0, gt=0)
    backoff_jitter: float = Field(default=1.0, ge=0, le=1)

    def get_retry(self) -> Retry:
        from httpx_retries import Retry

        return Retry(**self.model_dump(exclude_none=True))

    def get_transport(self) -> RetryTransport:
        from httpx_retries import RetryTransport

        return RetryTransport(retry=self.get_retry())
