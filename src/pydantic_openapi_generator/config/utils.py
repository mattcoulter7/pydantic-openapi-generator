import re
from typing import Any, Optional, Union

from openapi_pydantic.v3.v3_0 import Reference as Reference30
from openapi_pydantic.v3.v3_0 import Schema as Schema30
from openapi_pydantic.v3.v3_0.parameter import Parameter as Parameter30
from openapi_pydantic.v3.v3_1 import Reference as Reference31
from openapi_pydantic.v3.v3_1 import Schema as Schema31
from openapi_pydantic.v3.v3_1.parameter import Parameter as Parameter31

from pydantic_openapi_generator.language_converters.python import common
from pydantic_openapi_generator.language_converters.python.model_generator import (
    type_converter,
)

Parameter = Union[Parameter30, Parameter31]
SchemaDefinition = Union[Schema30, Schema31, Reference30, Reference31]


def lower_code_name(value: str) -> str:
    normalized = common.normalize_symbol(value)
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z0-9]|$)|[A-Z]?[a-z0-9]+", normalized)
    return "_".join(part.lower() for part in parts) or normalized.lower()


def literal_default(schema: Any) -> Optional[str]:
    default = getattr(schema, "default", None)
    return f"{default!r}" if default is not None else None


def optional_type(type_hint: str) -> str:
    if type_hint.startswith("Optional[") and type_hint.endswith("]"):
        return type_hint
    return f"Optional[{type_hint}]"


def parameter_type_hint(param: Parameter, required: bool) -> str:
    if isinstance(param.param_schema, (Schema30, Schema31, Reference30, Reference31)):
        return schema_type_hint(param.param_schema, required=required)
    return "Any"


def schema_type_hint(schema: SchemaDefinition, *, required: bool) -> str:
    if isinstance(schema, (Schema30, Schema31)):
        return type_converter(schema, required).converted_type
    if isinstance(schema, (Reference30, Reference31)):
        model_name = common.normalize_symbol(schema.ref.split("/")[-1])
        return model_name if required else f"Optional[{model_name}]"
    raise ValueError(f"Unsupported schema type: {type(schema)!r}")


def parameter_base_type_hint(param: Parameter) -> str:
    return parameter_type_hint(param, True)


def parameter_default(param: Parameter) -> Optional[str]:
    if isinstance(param.param_schema, (Schema30, Schema31)):
        return literal_default(param.param_schema)
    return None


def optional_override_type_and_default(param: Parameter) -> tuple[str, Optional[str]]:
    return optional_type(parameter_base_type_hint(param)), "None"
