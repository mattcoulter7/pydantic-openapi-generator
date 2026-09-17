from typing import Annotated, Literal, Optional, Union

from openapi_pydantic.v3.v3_0 import Reference as Reference30
from openapi_pydantic.v3.v3_0 import Schema as Schema30
from openapi_pydantic.v3.v3_1 import Reference as Reference31
from openapi_pydantic.v3.v3_1 import Schema as Schema31
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pydantic_openapi_generator.config.utils import literal_default, optional_type, schema_type_hint
from pydantic_openapi_generator.language_converters.python import common
from pydantic_openapi_generator.models import GeneratedCustomKwarg

SchemaDefinition = Union[Schema30, Schema31, Reference30, Reference31]


class CustomKwargExistsCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["exists"]
    ref: str


CustomKwargCondition = Annotated[CustomKwargExistsCondition, Field(discriminator="type")]


class CustomKwargConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kwarg: str
    required: bool = True
    schema_: SchemaDefinition = Field(alias="schema")
    condition: Optional[CustomKwargCondition] = None

    @model_validator(mode="after")
    def validate_supported_schema(self) -> "CustomKwargConfiguration":
        if schema_type_hint(self.schema_, required=True) == "Any":
            raise ValueError("custom_kwargs schema must define a supported OpenAPI schema")

        return self

    @property
    def code_name(self) -> str:
        return common.normalize_symbol(self.kwarg)

    def resolve_custom_kwarg(self) -> GeneratedCustomKwarg:
        default = literal_default(self.schema_)

        if self.required:
            type_hint = schema_type_hint(self.schema_, required=True)
            rendered_default = None
        elif default is not None:
            type_hint = schema_type_hint(self.schema_, required=True)
            rendered_default = default
        else:
            type_hint = optional_type(schema_type_hint(self.schema_, required=True))
            rendered_default = "None"

        return GeneratedCustomKwarg(
            code_name=self.code_name,
            type_hint=type_hint,
            required=self.required,
            default=rendered_default,
        )
