from typing import List, Literal, Optional, Union

from openapi_pydantic.v3.v3_0 import (
    Operation as Operation30,
)
from openapi_pydantic.v3.v3_0 import (
    PathItem as PathItem30,
)
from openapi_pydantic.v3.v3_0 import (
    Schema as Schema30,
)
from openapi_pydantic.v3.v3_1 import (
    Operation as Operation31,
)
from openapi_pydantic.v3.v3_1 import (
    PathItem as PathItem31,
)
from openapi_pydantic.v3.v3_1 import (
    Schema as Schema31,
)
from pydantic import BaseModel, Field

from pydantic_openapi_generator.config.parameter_source import ParameterSource

# Type unions for compatibility with both OpenAPI 3.0 and 3.1
Operation = Union[Operation30, Operation31]
PathItem = Union[PathItem30, PathItem31]
Schema = Union[Schema30, Schema31]


class LibraryConfig(BaseModel):
    name: str
    library_name: str
    template_name: str
    include_async: bool
    include_sync: bool


class TypeConversion(BaseModel):
    original_type: str
    converted_type: str
    import_types: Optional[List[str]] = None


class OpReturnType(BaseModel):
    type: Optional[TypeConversion] = None
    status_code: int
    complex_type: bool = False
    list_type: Optional[str] = None
    variants: List["ResponseVariant"] = Field(default_factory=list)
    status_handlers: List["ResponseStatusHandler"] = Field(default_factory=list)
    accept_content_types: List[str] = Field(default_factory=list)
    unambiguous_content_handlers: List["ResponseContentHandler"] = Field(default_factory=list)
    return_type_hint: Optional[str] = None


class ResponseVariant(BaseModel):
    status_code: int
    content_type: Optional[str] = None
    type: Optional[TypeConversion] = None
    complex_type: bool = False
    list_type: Optional[str] = None
    body_kind: Literal["empty", "json", "text", "binary"] = "empty"


class ResponseStatusHandler(BaseModel):
    status_code: int
    variants: List[ResponseVariant]


class ResponseContentHandler(BaseModel):
    content_type: str
    type: Optional[TypeConversion] = None
    complex_type: bool = False
    list_type: Optional[str] = None
    body_kind: Literal["json", "text", "binary"]


class RequestBodyDefinition(BaseModel):
    content_type: Optional[str] = None
    encoding: Literal["json", "form", "multipart", "binary", "text"]
    expression: str


class GeneratedParameter(BaseModel):
    wire_name: str
    code_name: str
    location: Literal["path", "query", "header", "cookie"]
    type_hint: str
    base_type_hint: str
    required: bool
    default: Optional[str] = None
    source: ParameterSource = ParameterSource.KWARG
    is_secret: bool = False
    field_type_hint: Optional[str] = None
    field_default: Optional[str] = None
    getter_name: Optional[str] = None
    local_name: Optional[str] = None
    value_expression: Optional[str] = None


class GeneratedCustomKwarg(BaseModel):
    code_name: str
    type_hint: str
    required: bool
    default: Optional[str] = None


class ServiceOperation(BaseModel):
    params: str
    call_kwargs: List[str] = Field(default_factory=list)
    operation_id: str
    path_params: List[GeneratedParameter] = Field(default_factory=list)
    query_params: List[GeneratedParameter]
    header_params: List[GeneratedParameter]
    custom_kwargs: List[GeneratedCustomKwarg] = Field(default_factory=list)
    return_type: OpReturnType
    operation: Operation
    pathItem: PathItem
    content: str
    async_client: Optional[bool] = False
    tag: Optional[str] = None
    path_name: str
    body_param: Optional[str] = None
    request_body: Optional[RequestBodyDefinition] = None
    method: str
    is_sse: bool = False
    sse_data_handler: Optional[ResponseContentHandler] = None
    use_orjson: bool = False


class Property(BaseModel):
    name: str
    type: TypeConversion
    required: bool
    default: Optional[str]
    import_type: Optional[List[str]] = None


class Model(BaseModel):
    file_name: str
    content: str
    openapi_object: Optional[Schema] = None
    properties: List[Property] = []


class ConversionResult(BaseModel):
    models: List[Model] = Field(default_factory=list)
    clients: List[Model] = Field(default_factory=list)
    exceptions: List[Model] = Field(default_factory=list)
