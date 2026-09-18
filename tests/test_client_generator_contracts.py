import asyncio
import importlib
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from pydantic_openapi_generator.common import HTTPLibrary
from pydantic_openapi_generator.generate_data import generate_data

CONTRACT_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Contract Test API", "version": "1.0.0"},
    "servers": [{"url": "http://testserver"}],
    "paths": {
        "/things": {
            "get": {
                "operationId": "getThing",
                "parameters": [
                    {
                        "name": "X-Test-Header",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "X-Optional-Header",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string", "default": "AU"},
                    },
                    {
                        "name": "X-Function-Header",
                        "in": "header",
                        "required": False,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "brand",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string", "default": "NRMA"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Thing",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ThingResponse"}
                            }
                        },
                    },
                    "206": {
                        "description": "Validation",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ValidationResponse"
                                }
                            }
                        },
                    },
                },
            }
        },
        "/upload": {
            "post": {
                "operationId": "uploadDocument",
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "string", "format": "binary"},
                                    "requestMetadata": {
                                        "type": "string",
                                        "format": "binary",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Uploaded",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/UploadResponse"
                                }
                            }
                        },
                    },
                    "202": {
                        "description": "Accepted",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/UploadResponse"
                                }
                            }
                        },
                    },
                },
            }
        },
        "/download": {
            "get": {
                "operationId": "downloadDocument",
                "responses": {
                    "200": {
                        "description": "PDF",
                        "content": {
                            "application/pdf": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    }
                },
            }
        },
        "/multi-download": {
            "get": {
                "operationId": "multiDownload",
                "responses": {
                    "200": {
                        "description": "Document",
                        "content": {
                            "application/json": {"schema": {"type": "string"}},
                            "application/pdf": {
                                "schema": {"type": "string", "format": "binary"}
                            },
                        },
                    }
                },
            }
        },
        "/empty": {
            "delete": {
                "operationId": "deleteThing",
                "responses": {"204": {"description": "Deleted"}},
            }
        },
        "/events": {
            "get": {
                "operationId": "getEvents",
                "responses": {
                    "200": {
                        "description": "Successful event stream connection.",
                        "content": {
                            "text/event-stream": {
                                "schema": {
                                    "type": "object",
                                    "required": ["data"],
                                    "properties": {
                                        "event": {"type": "string"},
                                        "data": {
                                            "$ref": "#/components/schemas/EventPayload"
                                        },
                                        "id": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ThingResponse": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
            "ValidationResponse": {
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
            },
            "UploadResponse": {
                "type": "object",
                "required": ["documentId"],
                "properties": {"documentId": {"type": "string"}},
            },
            "EventPayload": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "timestamp": {"type": "string", "format": "date-time"},
                    "value": {"type": "number"},
                },
            },
        }
    },
}


@pytest.fixture
def generated_contract_package():
    yield from _generated_package()


def _generated_package(config_content: str | None = None):
    package_name = f"contract_result_{uuid4().hex}"
    temp_dir = Path(tempfile.gettempdir()) / package_name
    spec_path = temp_dir.parent / f"{package_name}.json"
    config_path = temp_dir.parent / f"{package_name}.yaml"
    temp_dir.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(CONTRACT_SPEC))
    if config_content is not None:
        config_path.write_text(config_content)
    sys.path.insert(0, str(temp_dir.parent))

    try:
        generate_data(
            spec_path,
            temp_dir,
            HTTPLibrary.httpx,
            config_path=config_path if config_content is not None else None,
        )
        yield package_name
    finally:
        if str(temp_dir.parent) in sys.path:
            sys.path.remove(str(temp_dir.parent))
        shutil.rmtree(temp_dir, ignore_errors=True)
        spec_path.unlink(missing_ok=True)
        config_path.unlink(missing_ok=True)
        for module_name in list(sys.modules):
            if module_name == package_name or module_name.startswith(
                f"{package_name}."
            ):
                sys.modules.pop(module_name, None)


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_clients_honor_openapi_request_response_contracts(
    generated_contract_package,
    respx_mock,
    async_client,
):
    requests: list[httpx.Request] = []

    def thing_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(206, json={"message": "partial"})

    def upload_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"documentId": "doc-123"})

    def download_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=b"%PDF-1.4",
            headers={"content-type": "application/pdf"},
        )

    respx_mock.get("http://testserver/things").mock(side_effect=thing_handler)
    respx_mock.post("http://testserver/upload").mock(side_effect=upload_handler)
    respx_mock.get("http://testserver/download").mock(side_effect=download_handler)
    respx_mock.delete("http://testserver/empty").mock(return_value=httpx.Response(204))

    models = importlib.import_module(f"{generated_contract_package}.models")

    if async_client:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.async_client"
        )
        client = client_module.AsyncClient(timeout=1.23)
        thing, upload, download, empty = asyncio.run(_run_async_contract(client))
    else:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.sync_client"
        )
        client = client_module.SyncClient(timeout=1.23)
        thing = client.getThing(X_Test_Header="required")
        upload = client.uploadDocument(
            data={"file": ("doc.txt", b"hello", "text/plain")}
        )
        download = client.downloadDocument()
        empty = client.deleteThing()

    assert isinstance(thing, models.ValidationResponse)
    assert upload.documentId == "doc-123"
    assert download == b"%PDF-1.4"
    assert empty is None

    thing_request = requests[0]
    assert thing_request.headers["X-Test-Header"] == "required"
    assert thing_request.headers["X-Optional-Header"] == "AU"
    assert thing_request.url.params["brand"] == "NRMA"
    assert thing_request.extensions["timeout"] == {
        "connect": 1.23,
        "read": 1.23,
        "write": 1.23,
        "pool": 1.23,
    }

    upload_request = requests[1]
    assert upload_request.headers["content-type"].startswith("multipart/form-data")
    assert b'form-data; name="file"; filename="doc.txt"' in upload_request.content
    assert b"hello" in upload_request.content

    download_request = requests[2]
    assert download_request.headers["accept"] == "application/pdf"


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_clients_validate_undocumented_successes_against_contract(
    generated_contract_package,
    respx_mock,
    async_client,
):
    respx_mock.get("http://testserver/things").mock(
        return_value=httpx.Response(201, json={"unexpected": True})
    )
    respx_mock.post("http://testserver/upload").mock(
        return_value=httpx.Response(201, json={"documentId": "doc-201"})
    )

    if async_client:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.async_client"
        )
        client = client_module.AsyncClient()
        upload = asyncio.run(_run_async_undocumented_successes(client))
        with pytest.raises(ValidationError):
            asyncio.run(client.getThing(X_Test_Header="required"))
    else:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.sync_client"
        )
        client = client_module.SyncClient()
        upload = client.uploadDocument(
            data={"file": ("doc.txt", b"hello", "text/plain")}
        )
        with pytest.raises(ValidationError):
            client.getThing(X_Test_Header="required")

    assert upload.documentId == "doc-201"


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_clients_dispatch_same_status_by_content_type(
    generated_contract_package,
    respx_mock,
    async_client,
):
    respx_mock.get("http://testserver/multi-download").mock(
        side_effect=[
            httpx.Response(
                200,
                json="hello",
                headers={"content-type": "application/json; charset=utf-8"},
            ),
            httpx.Response(
                200,
                content=b"%PDF-1.4",
                headers={"content-type": "application/pdf"},
            ),
        ]
    )

    if async_client:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.async_client"
        )
        client = client_module.AsyncClient()
        json_result, pdf_result = asyncio.run(_run_async_multi_download(client))
    else:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.sync_client"
        )
        client = client_module.SyncClient()
        json_result = client.multiDownload()
        pdf_result = client.multiDownload()

    assert json_result == "hello"
    assert pdf_result == b"%PDF-1.4"


CONFIG_CONTENT = """
parameters:
  - name: X-Test-Header
    type: ClassVar
    code_name: test_header
    is_secret: true
  - name: X-Optional-Header
    type: ClassVar
    code_name: optional_header
  - name: X-Function-Header
    type: Function
    code_name: dynamic_header
  - name: brand
    type: Kwarg
    code_name: product_brand
"""


CUSTOM_KWARGS_CONFIG_CONTENT = """
parameters:
  - name: X-Test-Header
    type: ClassVar
    code_name: test_header
  - name: X-Function-Header
    type: Function
    code_name: dynamic_header
  - name: brand
    type: Kwarg
    code_name: product_brand

custom_kwargs:
  - kwarg: claimIdentifier
    required: true
    schema:
      type: string
    condition:
      type: exists
      ref: dynamic_header
  - kwarg: someContext
    required: false
    schema:
      type: integer
      format: int64
  - kwarg: labels
    required: false
    schema:
      type: array
      items:
        type: string
"""


@pytest.fixture
def generated_configured_contract_package():
    yield from _generated_package(CONFIG_CONTENT)


@pytest.fixture
def generated_custom_kwargs_contract_package():
    yield from _generated_package(CUSTOM_KWARGS_CONFIG_CONTENT)


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_clients_resolve_configured_parameter_sources(
    generated_configured_contract_package,
    respx_mock,
    async_client,
):
    requests: list[httpx.Request] = []

    def thing_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(206, json={"message": "partial"})

    respx_mock.get("http://testserver/things").mock(side_effect=thing_handler)

    if async_client:
        client_module = importlib.import_module(
            f"{generated_configured_contract_package}.clients.async_client"
        )

        class Client(client_module.AsyncClient):
            getter_calls: int = 0
            getter_kwargs: dict[str, Any] = {}
            token_kwargs: dict[str, Any] = {}

            async def get_dynamic_header(self, **kwargs: Any) -> str:
                self.getter_calls += 1
                self.getter_kwargs = kwargs
                return "from-getter"

            async def get_access_token(self, **kwargs: Any) -> str | None:
                self.token_kwargs = kwargs
                return None

        client = Client(test_header="from-client", optional_header="from-class")
        asyncio.run(_run_async_configured_contract(client))
        assert client.getter_calls == 1
    else:
        client_module = importlib.import_module(
            f"{generated_configured_contract_package}.clients.sync_client"
        )

        class Client(client_module.SyncClient):
            getter_calls: int = 0
            getter_kwargs: dict[str, Any] = {}
            token_kwargs: dict[str, Any] = {}

            def get_dynamic_header(self, **kwargs: Any) -> str:
                self.getter_calls += 1
                self.getter_kwargs = kwargs
                return "from-getter"

            def get_access_token(self, **kwargs: Any) -> str | None:
                self.token_kwargs = kwargs
                return None

        client = Client(test_header="from-client", optional_header="from-class")
        client.getThing(product_brand="CGU")
        client.getThing(
            test_header="from-override",
            optional_header="optional-override",
            dynamic_header="function-override",
            product_brand="AMI",
        )
        assert client.getter_calls == 1

    assert client.getter_kwargs == {
        "product_brand": "CGU",
        "test_header": None,
        "optional_header": None,
        "dynamic_header": None,
    }
    assert client.token_kwargs == {
        "product_brand": "AMI",
        "test_header": "from-override",
        "optional_header": "optional-override",
        "dynamic_header": "function-override",
    }

    first_request = requests[0]
    assert first_request.headers["X-Test-Header"] == "from-client"
    assert first_request.headers["X-Optional-Header"] == "from-class"
    assert first_request.headers["X-Function-Header"] == "from-getter"
    assert first_request.url.params["brand"] == "CGU"

    second_request = requests[1]
    assert second_request.headers["X-Test-Header"] == "from-override"
    assert second_request.headers["X-Optional-Header"] == "optional-override"
    assert second_request.headers["X-Function-Header"] == "function-override"
    assert second_request.url.params["brand"] == "AMI"

    assert "**********" not in first_request.headers["X-Test-Header"]
    assert "**********" not in second_request.headers["X-Test-Header"]


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_clients_pass_custom_kwargs_to_request_context_only(
    generated_custom_kwargs_contract_package,
    respx_mock,
    async_client,
):
    requests: list[httpx.Request] = []

    def thing_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(206, json={"message": "partial"})

    respx_mock.get("http://testserver/things").mock(side_effect=thing_handler)

    if async_client:
        client_module = importlib.import_module(
            f"{generated_custom_kwargs_contract_package}.clients.async_client"
        )

        class Client(client_module.AsyncClient):
            getter_kwargs: dict[str, Any] = {}
            token_kwargs: dict[str, Any] = {}

            async def get_dynamic_header(self, claimIdentifier: str, **kwargs: Any) -> str:
                self.getter_kwargs = {"claimIdentifier": claimIdentifier, **kwargs}
                return f"user-{claimIdentifier}"

            async def get_access_token(self, **kwargs: Any) -> str | None:
                self.token_kwargs = kwargs
                return None

        client = Client(test_header="from-client")
        asyncio.run(client.getThing(claimIdentifier="CLM-123", product_brand="CGU", someContext=42))
    else:
        client_module = importlib.import_module(
            f"{generated_custom_kwargs_contract_package}.clients.sync_client"
        )

        class Client(client_module.SyncClient):
            getter_kwargs: dict[str, Any] = {}
            token_kwargs: dict[str, Any] = {}

            def get_dynamic_header(self, claimIdentifier: str, **kwargs: Any) -> str:
                self.getter_kwargs = {"claimIdentifier": claimIdentifier, **kwargs}
                return f"user-{claimIdentifier}"

            def get_access_token(self, **kwargs: Any) -> str | None:
                self.token_kwargs = kwargs
                return None

        client = Client(test_header="from-client")
        client.getThing(claimIdentifier="CLM-123", product_brand="CGU", someContext=42)

    get_thing_signature = inspect.signature(client.getThing)
    assert str(get_thing_signature.parameters["claimIdentifier"].annotation) == "str"
    assert str(get_thing_signature.parameters["someContext"].annotation) == "Optional[int]"
    assert get_thing_signature.parameters["someContext"].default is None
    assert str(get_thing_signature.parameters["labels"].annotation) == "Optional[List[str]]"

    delete_signature = inspect.signature(client.deleteThing)
    assert "claimIdentifier" not in delete_signature.parameters
    assert "someContext" in delete_signature.parameters

    assert client.getter_kwargs == {
        "claimIdentifier": "CLM-123",
        "X_Optional_Header": "AU",
        "test_header": None,
        "dynamic_header": None,
        "product_brand": "CGU",
        "someContext": 42,
        "labels": None,
    }
    assert client.token_kwargs == client.getter_kwargs

    request = requests[0]
    assert request.headers["X-Test-Header"] == "from-client"
    assert request.headers["X-Function-Header"] == "user-CLM-123"
    assert request.url.params["brand"] == "CGU"
    assert "claimIdentifier" not in request.url.params
    assert "someContext" not in request.url.params
    assert "labels" not in request.url.params
    assert "claimIdentifier" not in request.headers
    assert "someContext" not in request.headers
    assert "labels" not in request.headers


@pytest.mark.respx(assert_all_called=False, assert_all_mocked=True)
@pytest.mark.parametrize("async_client", [False, True])
def test_generated_sse_data_payloads_use_declared_schema(
    generated_contract_package,
    respx_mock,
    async_client,
):
    requests: list[httpx.Request] = []

    def events_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                b"event: update\n"
                b'data: {"message": "ok", "timestamp": "2026-08-14T00:00:00Z", "value": 42.5}\n'
                b"\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    respx_mock.get("http://testserver/events").mock(
        side_effect=events_handler
    )
    models = importlib.import_module(f"{generated_contract_package}.models")

    if async_client:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.async_client"
        )
        client = client_module.AsyncClient(timeout=2.34)
        first_data_item = asyncio.run(_first_async_sse_data_item(client))
    else:
        client_module = importlib.import_module(
            f"{generated_contract_package}.clients.sync_client"
        )
        client = client_module.SyncClient(timeout=2.34)
        first_data_item = next(
            item for item in client.getEvents() if not isinstance(item, str)
        )

    assert isinstance(first_data_item, models.EventPayload)
    assert first_data_item.message == "ok"
    assert first_data_item.value == 42.5
    assert requests[0].extensions["timeout"] == {
        "connect": 2.34,
        "read": 2.34,
        "write": 2.34,
        "pool": 2.34,
    }


async def _run_async_configured_contract(client):
    await client.getThing(product_brand="CGU")
    await client.getThing(
        test_header="from-override",
        optional_header="optional-override",
        dynamic_header="function-override",
        product_brand="AMI",
    )


async def _first_async_sse_data_item(client):
    async for item in client.getEvents():
        if not isinstance(item, str):
            return item
    raise AssertionError("No typed SSE data item yielded")


async def _run_async_undocumented_successes(client):
    upload = await client.uploadDocument(
        data={"file": ("doc.txt", b"hello", "text/plain")}
    )
    return upload


async def _run_async_multi_download(client):
    json_result = await client.multiDownload()
    pdf_result = await client.multiDownload()
    return json_result, pdf_result


def test_duplicate_configured_code_name_fails_generation():
    config_content = """
parameters:
  - name: X-Test-Header
    type: ClassVar
    code_name: shared
  - name: X-Optional-Header
    type: ClassVar
    code_name: shared
"""

    with pytest.raises(ValueError, match="code_name 'shared'"):
        list(_generated_package(config_content))


def test_custom_kwarg_without_schema_fails_config_validation():
    config_content = """
custom_kwargs:
  - kwarg: claimIdentifier
    required: true
"""

    with pytest.raises(ValidationError, match="schema"):
        list(_generated_package(config_content))


def test_custom_kwarg_schema_that_converts_to_any_fails_config_validation():
    config_content = """
custom_kwargs:
  - kwarg: claimIdentifier
    required: true
    schema: {}
"""

    with pytest.raises(ValidationError, match="must define a supported OpenAPI schema"):
        list(_generated_package(config_content))


def test_duplicate_custom_kwarg_names_fail_config_validation():
    config_content = """
custom_kwargs:
  - kwarg: claimIdentifier
    required: true
    schema:
      type: string
  - kwarg: claimIdentifier
    required: true
    schema:
      type: string
"""

    with pytest.raises(ValueError, match="Duplicate custom kwarg configuration for: claimIdentifier"):
        list(_generated_package(config_content))


def test_custom_kwarg_collision_with_api_parameter_fails_generation():
    config_content = """
parameters:
  - name: brand
    type: Kwarg
    code_name: product_brand
custom_kwargs:
  - kwarg: product_brand
    required: true
    schema:
      type: string
"""

    with pytest.raises(ValueError, match="collides with generated API parameter code_name: product_brand"):
        list(_generated_package(config_content))


async def _run_async_contract(client):
    thing = await client.getThing(X_Test_Header="required")
    upload = await client.uploadDocument(
        data={"file": ("doc.txt", b"hello", "text/plain")}
    )
    download = await client.downloadDocument()
    empty = await client.deleteThing()
    return thing, upload, download, empty
