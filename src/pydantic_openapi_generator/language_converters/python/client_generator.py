import logging
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from openapi_pydantic.v3 import (
    Operation,
    PathItem,
    Reference,
    Response,
    Schema,
)
from openapi_pydantic.v3.v3_0 import (
    MediaType as MediaType30,
)

# Import version-specific types for isinstance checks
from openapi_pydantic.v3.v3_0 import (
    Reference as Reference30,
)
from openapi_pydantic.v3.v3_0 import (
    Response as Response30,
)
from openapi_pydantic.v3.v3_0 import (
    Schema as Schema30,
)
from openapi_pydantic.v3.v3_0.parameter import Parameter as Parameter30
from openapi_pydantic.v3.v3_1 import (
    MediaType as MediaType31,
)
from openapi_pydantic.v3.v3_1 import (
    Reference as Reference31,
)
from openapi_pydantic.v3.v3_1 import (
    Response as Response31,
)
from openapi_pydantic.v3.v3_1 import (
    Schema as Schema31,
)
from openapi_pydantic.v3.v3_1.parameter import Parameter as Parameter31

from pydantic_openapi_generator.common import PydanticVersion
from pydantic_openapi_generator.config.custom_kwarg import CustomKwargConfiguration
from pydantic_openapi_generator.config.generator_config import (
    PydanticOpenAPIGeneratorConfig,
)
from pydantic_openapi_generator.config.parameter_source import ParameterSource
from pydantic_openapi_generator.language_converters.python import common
from pydantic_openapi_generator.language_converters.python.jinja_config import (
    ASYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2,
    SYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2,
    create_jinja_env,
)
from pydantic_openapi_generator.language_converters.python.model_generator import (
    type_converter,
)
from pydantic_openapi_generator.models import (
    GeneratedCustomKwarg,
    GeneratedParameter,
    LibraryConfig,
    Model,
    OpReturnType,
    RequestBodyDefinition,
    ResponseContentHandler,
    ResponseVariant,
    ServiceOperation,
    TypeConversion,
)

RESERVED_CLIENT_MEMBER_NAMES = {
    "base_url",
    "verify",
    "access_token",
    "get_access_token",
    "set_access_token",
    "model_config",
}


# Helper functions for isinstance checks across OpenAPI versions
def is_response_type(obj) -> bool:
    """Check if object is a Response from any OpenAPI version"""
    return isinstance(obj, (Response30, Response31))


def create_media_type_for_reference(
    reference_obj: Union[Response30, Reference30, Response31, Reference31],
):
    """Create a MediaType wrapper for a reference object, using the correct version"""
    # Check which version the reference object belongs to
    if isinstance(reference_obj, Reference30):
        return MediaType30(schema=reference_obj)  # type: ignore - pydantic issue with generics
    elif isinstance(reference_obj, Reference31):
        return MediaType31(schema=reference_obj)  # type: ignore - pydantic issue with generics
    else:
        # Fallback to v3.0 for generic Reference
        return MediaType30(schema=reference_obj)  # type: ignore - pydantic issue with generics


def is_media_type(obj) -> bool:
    """Check if object is a MediaType from any OpenAPI version"""
    return isinstance(obj, (MediaType30, MediaType31))


def is_reference_type(obj: Any) -> bool:
    """Check if object is a Reference type across different versions."""
    return isinstance(obj, (Reference, Reference30, Reference31))


def is_schema_type(obj: Any) -> bool:
    """Check if object is a Schema from any OpenAPI version"""
    return isinstance(obj, (Schema30, Schema31))


def _common_suffix(a: str, b: str) -> str:
    i = 1
    while i <= min(len(a), len(b)) and a[-i] == b[-i]:
        i += 1
    return a[-(i - 1) :] if i > 1 else ""


def _common_suffix_many(names: List[str]) -> str:
    if not names:
        return ""
    suf = names[0]
    for n in names[1:]:
        suf = _common_suffix(suf, n)
        if not suf:
            break
    return suf


def operation_is_sse(op: Operation) -> bool:
    """Detect if an Operation advertises Server-Sent-Events (text/event-stream) in any 2xx response."""
    if not getattr(op, "responses", None):
        return False

    for status_code, resp in op.responses.items():
        try:
            if not str(status_code).startswith("2"):
                continue
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.debug("Skipping response status key; conversion failed", exc_info=e)
            continue

        # Concrete Response object
        if is_response_type(resp):
            content = getattr(resp, "content", None)
            if isinstance(content, dict) and "text/event-stream" in content:
                return True

        # Reference responses could be resolved externally; skip for now
        if is_reference_type(resp):
            # If you need supporting $ref'ed SSE responses, resolve via components
            pass

    return False


def _sse_data_schema_from_media_type(media_type: Any) -> Any:
    media_type_schema = getattr(media_type, "media_type_schema", None)
    if media_type_schema is None:
        return None

    if is_reference_type(media_type_schema):
        return media_type_schema

    if not is_schema_type(media_type_schema):
        return None

    properties = getattr(media_type_schema, "properties", None)
    if isinstance(properties, dict) and "data" in properties:
        return properties["data"]

    schema_type = getattr(media_type_schema, "type", None)
    if schema_type != "object" and str(schema_type) != "DataType.OBJECT":
        return media_type_schema

    return None


def generate_sse_data_handler(operation: Operation) -> Optional[ResponseContentHandler]:
    if not getattr(operation, "responses", None):
        return None

    for status_code, response in operation.responses.items():
        try:
            if not str(status_code).startswith("2"):
                continue
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.debug("Skipping response status key; conversion failed", exc_info=e)
            continue

        if not is_response_type(response):
            continue

        content = getattr(response, "content", None)
        if not isinstance(content, dict):
            continue

        media_type = content.get("text/event-stream")
        if not is_media_type(media_type):
            continue

        data_schema = _sse_data_schema_from_media_type(media_type)
        if data_schema is None:
            continue

        variant = _response_variant_from_schema(int(status_code), "application/json", data_schema)
        if variant.type is None or variant.body_kind == "empty":
            continue

        return ResponseContentHandler(
            content_type="text/event-stream",
            type=variant.type,
            complex_type=variant.complex_type,
            list_type=variant.list_type,
            body_kind=variant.body_kind,  # type: ignore[arg-type]
        )

    return None


HTTP_OPERATIONS = ["get", "post", "put", "delete", "options", "head", "patch", "trace"]


def _json_body_expression(media_type_schema: Any) -> str:
    if isinstance(media_type_schema, (Reference, Reference30, Reference31)) or hasattr(media_type_schema, "ref"):
        return "data.model_dump(by_alias=True, exclude_none=True)"

    if isinstance(media_type_schema, (Schema, Schema30, Schema31)):
        schema = media_type_schema

        if schema.type == "array":
            return "[i.model_dump(by_alias=True, exclude_none=True) if hasattr(i, 'model_dump') else i for i in data]"

        if schema.type == "object":
            return "data.model_dump(by_alias=True, exclude_none=True) if hasattr(data, 'model_dump') else data"

        return "data"

    raise Exception(f"Unsupported schema type for request body: {type(media_type_schema)}")  # pragma: no cover


def generate_request_body(operation: Operation) -> Union[RequestBodyDefinition, None]:
    if operation.requestBody is None:
        return None

    # If requestBody is a $ref, it will be a Pydantic model instance in the client.
    if isinstance(operation.requestBody, (Reference30, Reference31)):
        return RequestBodyDefinition(
            content_type="application/json",
            encoding="json",
            expression="data.model_dump(by_alias=True, exclude_none=True)",
        )

    rb_content = getattr(operation.requestBody, "content", None)
    if rb_content is None:
        return None  # pragma: no cover

    ordered_content_types = [
        "application/json",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "application/octet-stream",
        "text/plain",
    ]
    content_type = next((ct for ct in ordered_content_types if rb_content.get(ct) is not None), None)
    if content_type is None:
        return None

    media_type = rb_content.get(content_type)
    if media_type is None:
        return None  # pragma: no cover

    mts = getattr(media_type, "media_type_schema", None)
    if content_type == "application/json":
        if mts is None:
            return None  # pragma: no cover
        return RequestBodyDefinition(
            content_type=content_type,
            encoding="json",
            expression=_json_body_expression(mts),
        )

    if content_type == "multipart/form-data":
        return RequestBodyDefinition(content_type=None, encoding="multipart", expression="data")

    if content_type == "application/x-www-form-urlencoded":
        return RequestBodyDefinition(content_type=content_type, encoding="form", expression="data")

    if content_type == "application/octet-stream":
        return RequestBodyDefinition(content_type=content_type, encoding="binary", expression="data")

    if content_type == "text/plain":
        return RequestBodyDefinition(content_type=content_type, encoding="text", expression="data")

    return None  # pragma: no cover


def generate_body_param(operation: Operation) -> Union[str, None]:
    request_body = generate_request_body(operation)
    return None if request_body is None else request_body.expression


def _resolve_parameter(
    param: Union[Parameter30, Parameter31],
    config: PydanticOpenAPIGeneratorConfig,
) -> GeneratedParameter:
    return config.parameter_configuration_for(param.name).resolve_parameter(param)


SignatureParameter = Union[GeneratedParameter, GeneratedCustomKwarg]


def _method_signature(parameters: List[SignatureParameter], body_param: Optional[str]) -> str:
    required_params: List[str] = []
    default_params: List[str] = []

    for parameter in parameters:
        rendered = f"{parameter.code_name}: {parameter.type_hint}"
        if parameter.default is None:
            required_params.append(rendered)
        else:
            default_params.append(f"{rendered} = {parameter.default}")

    if body_param is not None:
        required_params.append(body_param)

    all_params = required_params + default_params
    return ", ".join(all_params) + (", " if all_params else "")


def _body_signature_param(operation: Operation) -> Optional[str]:
    if operation.requestBody is None or is_reference_type(operation.requestBody):
        if isinstance(operation.requestBody, (Reference, Reference30, Reference31)):
            return f"data : {operation.requestBody.ref.split('/')[-1]}"  # type: ignore
        return None

    rb_content = getattr(operation.requestBody, "content", None)
    operation_request_body_types = [
        "application/json",
        "text/plain",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "application/octet-stream",
    ]
    if not isinstance(rb_content, dict):
        return None
    content_type = next((i for i in operation_request_body_types if rb_content.get(i)), None)
    if content_type is None:
        return None
    content = rb_content.get(content_type)
    if content is None or not hasattr(content, "media_type_schema"):
        return None
    mts = getattr(content, "media_type_schema", None)
    if isinstance(mts, (Reference, Reference30, Reference31)):
        return f"data : {mts.ref.split('/')[-1]}"  # type: ignore
    if isinstance(mts, (Schema, Schema30, Schema31)):
        return f"data : {type_converter(mts, True).converted_type}"  # type: ignore
    return None


def _parameter_dict_items(parameters: List[GeneratedParameter]) -> List[str]:
    return [f"{p.wire_name!r}: {p.value_expression}" for p in parameters]


def _custom_kwarg_applies(
    custom_kwarg: CustomKwargConfiguration,
    parameters: List[GeneratedParameter],
) -> bool:
    condition = custom_kwarg.condition
    if condition is None:
        return True

    if condition.type == "exists":
        ref = common.normalize_symbol(condition.ref)
        return any(parameter.code_name == ref for parameter in parameters)

    return False


def resolve_custom_kwargs(
    configured_custom_kwargs: List[CustomKwargConfiguration],
    parameters: List[GeneratedParameter],
) -> List[GeneratedCustomKwarg]:
    custom_kwargs = [
        custom_kwarg.resolve_custom_kwarg()
        for custom_kwarg in configured_custom_kwargs
        if _custom_kwarg_applies(custom_kwarg, parameters)
    ]

    parameter_code_names = {parameter.code_name for parameter in parameters}
    collisions = sorted(
        custom_kwarg.code_name for custom_kwarg in custom_kwargs if custom_kwarg.code_name in parameter_code_names
    )
    if collisions:
        collision_list = ", ".join(collisions)
        raise ValueError(f"Custom kwarg collides with generated API parameter code_name: {collision_list}")

    return custom_kwargs


def _resolved_path_name(path_name: str, path_params: List[GeneratedParameter]) -> str:
    resolved = path_name
    for parameter in path_params:
        resolved = re.sub(
            r"\{" + re.escape(parameter.wire_name) + r"\}",
            "{" + (parameter.value_expression or parameter.code_name) + "}",
            resolved,
        )
    return clean_up_path_name(resolved)


def _collect_client_parameters(
    operations: List[ServiceOperation],
    source: ParameterSource,
) -> List[GeneratedParameter]:
    collected: Dict[str, GeneratedParameter] = {}
    wire_by_code_name: Dict[str, str] = {}

    for operation in operations:
        for parameter in operation.path_params + operation.query_params + operation.header_params:
            if parameter.source != source:
                continue

            if parameter.code_name in RESERVED_CLIENT_MEMBER_NAMES:
                raise ValueError(f"Configured parameter code_name {parameter.code_name!r} is reserved")

            existing_wire_name = wire_by_code_name.get(parameter.code_name)
            if existing_wire_name is not None and existing_wire_name != parameter.wire_name:
                raise ValueError(
                    f"Configured parameter code_name {parameter.code_name!r} is used by both "
                    f"{existing_wire_name!r} and {parameter.wire_name!r}"
                )

            wire_by_code_name[parameter.code_name] = parameter.wire_name
            existing = collected.get(parameter.code_name)
            if existing is None:
                collected[parameter.code_name] = parameter
                continue

            if not existing.required and parameter.required:
                existing.required = True
                existing.field_type_hint = parameter.field_type_hint
                existing.field_default = parameter.field_default

    return list(collected.values())


def generate_params(operation: Operation) -> str:
    def _schema_default(schema: Any) -> Any:
        default = getattr(schema, "default", None)
        if default is None:
            return None
        return default

    def _default_suffix(schema: Any, required: bool) -> str:
        if required:
            return ""
        default = _schema_default(schema)
        return f" = {default!r}" if default is not None else " = None"

    def _generate_params_from_content(content: Any):
        # Accept reference from either 3.0 or 3.1
        if isinstance(content, (Reference, Reference30, Reference31)):
            return f"data : {content.ref.split('/')[-1]}"  # type: ignore
        elif isinstance(content, (Schema, Schema30, Schema31)):
            return f"data : {type_converter(content, True).converted_type}"  # type: ignore
        else:  # pragma: no cover
            raise Exception(f"Unsupported request body schema type: {type(content)}")

    if operation.parameters is None and operation.requestBody is None:
        return ""

    params = ""
    default_params = ""
    if operation.parameters is not None:
        for param in operation.parameters:
            if not isinstance(param, (Parameter30, Parameter31)):
                continue  # pragma: no cover
            converted_result = ""
            required = False
            param_name_cleaned = common.normalize_symbol(param.name)

            if isinstance(param.param_schema, Schema30) or isinstance(param.param_schema, Schema31):
                converted_result = (
                    f"{param_name_cleaned} : {type_converter(param.param_schema, param.required).converted_type}"
                    + _default_suffix(param.param_schema, param.required)
                )
                required = param.required
            elif isinstance(param.param_schema, Reference30) or isinstance(param.param_schema, Reference31):
                converted_result = f"{param_name_cleaned} : {param.param_schema.ref.split('/')[-1]}" + (
                    ""
                    if isinstance(param, Reference30) or isinstance(param, Reference31) or param.required
                    else " = None"
                )
                required = isinstance(param, Reference) or param.required

            if required:
                params += f"{converted_result}, "
            else:
                default_params += f"{converted_result}, "

    operation_request_body_types = [
        "application/json",
        "text/plain",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "application/octet-stream",
    ]

    if operation.requestBody is not None and not is_reference_type(operation.requestBody):
        # Safe access only if it's a concrete RequestBody object
        rb_content = getattr(operation.requestBody, "content", None)
        if isinstance(rb_content, dict) and any(rb_content.get(i) is not None for i in operation_request_body_types):
            get_keyword = [i for i in operation_request_body_types if rb_content.get(i)][0]
            content = rb_content.get(get_keyword)
            if content is not None and hasattr(content, "media_type_schema"):
                mts = getattr(content, "media_type_schema", None)
                if isinstance(
                    mts,
                    (Reference, Reference30, Reference31, Schema, Schema30, Schema31),
                ):
                    params += f"{_generate_params_from_content(mts)}, "
                else:  # pragma: no cover
                    raise Exception(f"Unsupported media type schema for {str(operation)}: {type(mts)}")
        # else: silently ignore unsupported body shapes (could extend later)
    # Replace - with _ in params
    params = params.replace("-", "_")
    default_params = default_params.replace("-", "_")

    return params + default_params


def generate_operation_id(operation: Operation, http_op: str, path_name: Optional[str] = None) -> str:
    if operation.operationId is not None:
        return common.normalize_symbol(operation.operationId)
    elif path_name is not None:
        return common.normalize_symbol(f"{http_op}_{path_name}")
    else:
        raise Exception(
            f"OperationId is not defined for {http_op} of path_name {path_name} --> {operation.summary}"
        )  # pragma: no cover


def _generate_params(operation: Operation, param_in: Literal["query", "header"] = "query"):
    if operation.parameters is None:
        return []

    params = []
    for param in operation.parameters:
        if isinstance(param, (Parameter30, Parameter31)) and param.param_in == param_in:
            param_name_cleaned = common.normalize_symbol(param.name)
            params.append(f"{param.name!r} : {param_name_cleaned}")

    return params


def generate_query_params(operation: Operation) -> List[str]:
    return _generate_params(operation, "query")


def generate_header_params(operation: Operation) -> List[str]:
    return _generate_params(operation, "header")


def generate_operation_parameters(
    operation: Operation,
    config: PydanticOpenAPIGeneratorConfig,
    param_in: Optional[Literal["path", "query", "header", "cookie"]] = None,
) -> List[GeneratedParameter]:
    if operation.parameters is None:
        return []

    parameters: List[GeneratedParameter] = []
    for param in operation.parameters:
        if not isinstance(param, (Parameter30, Parameter31)):
            continue
        if param_in is not None and param.param_in != param_in:
            continue
        parameters.append(_resolve_parameter(param, config))

    return parameters


def _is_binary_schema(schema: Any) -> bool:
    schema_format = getattr(schema, "schema_format", None)
    schema_type = getattr(schema, "type", None)
    return (schema_type == "string" or str(schema_type) == "DataType.STRING") and schema_format == "binary"


def _body_kind_for_content(
    content_type: Optional[str], schema: Any = None
) -> Literal["empty", "json", "text", "binary"]:
    if content_type is None:
        return "empty"
    lowered = content_type.lower()
    if lowered == "application/json" or lowered.endswith("+json"):
        return "json"
    if lowered == "application/pdf" or lowered == "application/octet-stream" or _is_binary_schema(schema):
        return "binary"
    if lowered.startswith("text/"):
        return "text"
    return "binary"


def _response_variant_from_schema(
    status_code: int,
    content_type: Optional[str],
    inner_schema: Any,
) -> ResponseVariant:
    body_kind = _body_kind_for_content(content_type, inner_schema)
    if body_kind == "empty":
        return ResponseVariant(status_code=status_code, content_type=content_type, body_kind="empty")

    if body_kind == "binary":
        return ResponseVariant(
            status_code=status_code,
            content_type=content_type,
            type=TypeConversion(original_type=content_type or "binary", converted_type="bytes"),
            body_kind="binary",
        )

    if body_kind == "text":
        return ResponseVariant(
            status_code=status_code,
            content_type=content_type,
            type=TypeConversion(original_type=content_type or "text", converted_type="str"),
            body_kind="text",
        )

    if is_reference_type(inner_schema):
        type_conv = TypeConversion(
            original_type=inner_schema.ref,  # type: ignore
            converted_type=inner_schema.ref.split("/")[-1],  # type: ignore
            import_types=[inner_schema.ref.split("/")[-1]],  # type: ignore
        )
        return ResponseVariant(
            status_code=status_code,
            content_type=content_type,
            type=type_conv,
            complex_type=True,
            body_kind="json",
        )

    if is_schema_type(inner_schema):
        disc = getattr(inner_schema, "discriminator", None)
        used = getattr(inner_schema, "oneOf", None) or getattr(inner_schema, "anyOf", None)
        disc_key = getattr(disc, "propertyName", None) if disc is not None else None

        if disc_key and used and all(is_reference_type(s) for s in used):
            member_models = [common.normalize_symbol(s.ref.split("/")[-1]) for s in used]  # type: ignore
            alias_name = common.normalize_symbol(_common_suffix_many(member_models)) or "Response"

            type_conv = TypeConversion(
                original_type="discriminated_union",
                converted_type=alias_name,
                import_types=None,
            )
            return ResponseVariant(
                status_code=status_code,
                content_type=content_type,
                type=type_conv,
                complex_type=True,
                body_kind="json",
            )

        converted_result = type_converter(inner_schema, True)  # type: ignore
        list_type = None
        if "array" in converted_result.original_type and isinstance(converted_result.import_types, list):
            matched = re.findall(r"List\[(.+)\]", converted_result.converted_type)
            if len(matched) > 0:
                list_type = matched[0]
            else:  # pragma: no cover
                raise Exception(f"Unable to parse list type from {converted_result.converted_type}")

        return ResponseVariant(
            status_code=status_code,
            content_type=content_type,
            type=converted_result,
            complex_type=bool(converted_result.import_types and len(converted_result.import_types) > 0),
            list_type=list_type,
            body_kind="json",
        )

    return ResponseVariant(status_code=status_code, content_type=content_type, body_kind="empty")


def _response_variants_for_response(status_code: int, response: Union[Response, Reference]) -> List[ResponseVariant]:
    if is_reference_type(response):
        media_type = create_media_type_for_reference(response)
        inner_schema = getattr(media_type, "media_type_schema", None)
        return [_response_variant_from_schema(status_code, "application/json", inner_schema)]

    if not is_response_type(response):
        return []

    content = getattr(response, "content", None)
    if not isinstance(content, dict) or not content:
        return [ResponseVariant(status_code=status_code, content_type=None, body_kind="empty")]

    variants: List[ResponseVariant] = []
    for content_type, media_type in content.items():
        if not is_media_type(media_type):
            continue
        inner_schema = getattr(media_type, "media_type_schema", None)
        variants.append(_response_variant_from_schema(status_code, content_type, inner_schema))

    return variants or [ResponseVariant(status_code=status_code, content_type=None, body_kind="empty")]


def _return_type_hint(variants: List[ResponseVariant]) -> str:
    hints: List[str] = []
    for variant in variants:
        if variant.body_kind == "empty" or variant.type is None:
            hint = "None"
        elif variant.list_type is not None:
            hint = f"list[{variant.list_type}]"
        else:
            hint = variant.type.converted_type
        if hint not in hints:
            hints.append(hint)
    if not hints:
        return "None"
    return hints[0] if len(hints) == 1 else " | ".join(hints)


def _variant_deserialization_key(variant: ResponseVariant) -> tuple:
    return (
        variant.body_kind,
        variant.type.converted_type if variant.type is not None else None,
        variant.complex_type,
        variant.list_type,
    )


def _unambiguous_content_handlers(
    variants: List[ResponseVariant],
) -> List[ResponseContentHandler]:
    variants_by_content_type: Dict[str, List[ResponseVariant]] = {}
    for variant in variants:
        if variant.content_type is None or variant.body_kind == "empty":
            continue
        variants_by_content_type.setdefault(variant.content_type.lower(), []).append(variant)

    handlers: List[ResponseContentHandler] = []
    for content_type, content_variants in variants_by_content_type.items():
        keys = {_variant_deserialization_key(variant) for variant in content_variants}
        if len(keys) != 1:
            continue

        variant = content_variants[0]
        handlers.append(
            ResponseContentHandler(
                content_type=content_type,
                type=variant.type,
                complex_type=variant.complex_type,
                list_type=variant.list_type,
                body_kind=variant.body_kind,  # type: ignore[arg-type]
            )
        )

    return handlers


def generate_return_type(operation: Operation) -> OpReturnType:
    if operation.responses is None:
        return OpReturnType(type=None, status_code=200, complex_type=False)

    good_responses: List[Tuple[int, Union[Response, Reference]]] = [
        (int(status_code), response)
        for status_code, response in operation.responses.items()
        if status_code.startswith("2")
    ]
    if len(good_responses) == 0:
        return OpReturnType(type=None, status_code=200, complex_type=False)

    variants: List[ResponseVariant] = []
    for status_code, response in good_responses:
        variants.extend(_response_variants_for_response(status_code, response))

    first_variant = variants[0] if variants else ResponseVariant(status_code=good_responses[0][0])
    return OpReturnType(
        type=first_variant.type,
        status_code=first_variant.status_code,
        complex_type=first_variant.complex_type,
        list_type=first_variant.list_type,
        variants=variants,
        accept_content_types=list(dict.fromkeys(v.content_type for v in variants if v.content_type is not None)),
        unambiguous_content_handlers=_unambiguous_content_handlers(variants),
        return_type_hint=_return_type_hint(variants),
    )


def clean_up_path_name(path_name: str) -> str:
    # Clean up path name: only replace dashes inside curly brackets for f-string compatibility, keep other dashes
    def _replace_bracket_dashes(match):
        return "{" + match.group(1).replace("-", "_") + "}"

    return re.sub(r"\{([^}/]+)\}", _replace_bracket_dashes, path_name)


def generate_clients(
    openapi: Any,
    paths: Dict[str, PathItem],
    library_config: LibraryConfig,
    env_token_name: Optional[str],
    pydantic_version: PydanticVersion,
    config: Optional[PydanticOpenAPIGeneratorConfig] = None,
) -> List[Model]:
    """
    Generate two client modules:
      - sync_client.py (SyncClient)
      - async_client.py (AsyncClient)
    """
    jinja_env = create_jinja_env()

    service_ops: List[ServiceOperation] = []
    generator_config = config or PydanticOpenAPIGeneratorConfig()

    def _generate_service_operation(
        op: Operation,
        path_obj: PathItem,
        path_name: str,
        http_operation: str,
        async_type: bool,
    ) -> ServiceOperation:
        path_level_params = []
        if hasattr(path_obj, "parameters") and path_obj.parameters is not None:
            path_level_params = [p for p in path_obj.parameters if p is not None]
        if path_level_params:
            existing_names = set()
            if op.parameters is not None:
                for p in op.parameters:
                    if isinstance(p, (Parameter30, Parameter31)):
                        existing_names.add(p.name)
            for p in path_level_params:
                if isinstance(p, (Parameter30, Parameter31)) and p.name not in existing_names:
                    if op.parameters is None:
                        op.parameters = []  # type: ignore
                    op.parameters.append(p)  # type: ignore

        request_body = generate_request_body(op)
        body_param = None if request_body is None else request_body.expression
        path_params = generate_operation_parameters(op, generator_config, "path")
        query_params = generate_operation_parameters(op, generator_config, "query")
        header_params = generate_operation_parameters(op, generator_config, "header")
        api_params = path_params + query_params + header_params
        custom_kwargs = resolve_custom_kwargs(generator_config.custom_kwargs, api_params)
        signature_params: List[SignatureParameter] = api_params + custom_kwargs
        call_kwargs = [param.code_name for param in signature_params]
        params = _method_signature(signature_params, _body_signature_param(op) if body_param is not None else None)
        path_name = _resolved_path_name(path_name, path_params)

        placeholder_names = [m.group(1) for m in re.finditer(r"\{([^}/]+)\}", path_name)]
        existing_param_names = {p.code_name for p in signature_params}
        for ph in placeholder_names:
            norm_ph = common.normalize_symbol(ph)
            if norm_ph not in existing_param_names and norm_ph:
                params = f"{norm_ph}: Any, " + params
                call_kwargs.insert(0, norm_ph)
        if body_param is not None:
            call_kwargs.append("data")

        operation_id = generate_operation_id(op, http_operation, path_name)
        return_type = generate_return_type(op)

        so = ServiceOperation(
            params=params,
            call_kwargs=call_kwargs,
            operation_id=operation_id,
            path_params=path_params,
            query_params=query_params,
            header_params=header_params,
            custom_kwargs=custom_kwargs,
            return_type=return_type,
            operation=op,
            pathItem=path_obj,
            content="",
            async_client=async_type,
            body_param=body_param,
            request_body=request_body,
            path_name=path_name,
            method=http_operation,
            is_sse=operation_is_sse(op),
            sse_data_handler=generate_sse_data_handler(op),
            use_orjson=common.get_use_orjson(),
        )

        return so

    for path_name, path in paths.items():
        clean_path_name = clean_up_path_name(path_name)
        for http_operation in HTTP_OPERATIONS:
            op = getattr(path, http_operation)
            if op is None:
                continue

            if library_config.include_sync:
                service_ops.append(_generate_service_operation(op, path, clean_path_name, http_operation, False))
            if library_config.include_async:
                service_ops.append(_generate_service_operation(op, path, clean_path_name, http_operation, True))

    sync_ops = [so for so in service_ops if not so.async_client]
    async_ops = [so for so in service_ops if so.async_client]
    client_fields = _collect_client_parameters(service_ops, ParameterSource.CLASS_VAR)
    client_functions = _collect_client_parameters(service_ops, ParameterSource.FUNCTION)

    openapi_dump = openapi.model_dump() if hasattr(openapi, "model_dump") else {}

    sync_content = jinja_env.get_template(SYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2).render(
        **openapi_dump,
        env_token_name=env_token_name,
        operations=[so.model_dump() for so in sync_ops],
        client_fields=[p.model_dump() for p in client_fields],
        client_functions=[p.model_dump() for p in client_functions],
    )
    async_content = jinja_env.get_template(ASYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2).render(
        **openapi_dump,
        env_token_name=env_token_name,
        operations=[so.model_dump() for so in async_ops],
        client_fields=[p.model_dump() for p in client_fields],
        client_functions=[p.model_dump() for p in client_functions],
    )

    compile(sync_content, "<string>", "exec")
    compile(async_content, "<string>", "exec")

    clients: List[Model] = [
        Model(
            file_name="sync_client",
            content=sync_content,
            openapi_object={},
            properties=[],
        ),
        Model(
            file_name="async_client",
            content=async_content,
            openapi_object={},
            properties=[],
        ),
    ]

    return clients
