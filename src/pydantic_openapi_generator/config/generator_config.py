from pydantic import BaseModel, ConfigDict, Field, model_validator

from pydantic_openapi_generator.config.base_parameter import BaseArgumentConfiguration
from pydantic_openapi_generator.config.custom_kwarg import CustomKwargConfiguration
from pydantic_openapi_generator.config.kwarg_parameter import KwargArgumentConfiguration
from pydantic_openapi_generator.config.parameter_union import ArgumentConfiguration


class PydanticOpenAPIGeneratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: list[ArgumentConfiguration] = Field(default_factory=list)
    custom_kwargs: list[CustomKwargConfiguration] = Field(default_factory=list)

    def parameter_configuration_for(self, parameter_name: str) -> BaseArgumentConfiguration:
        configured = self.parameters_by_name().get(parameter_name)
        return configured if configured is not None else KwargArgumentConfiguration(name=parameter_name)

    def parameters_by_name(self) -> dict[str, ArgumentConfiguration]:
        return {parameter.name: parameter for parameter in self.parameters}

    @model_validator(mode="after")
    def validate_unique_parameter_names(self) -> "PydanticOpenAPIGeneratorConfig":
        names: set[str] = set()
        duplicates: set[str] = set()
        for parameter in self.parameters:
            if parameter.name in names:
                duplicates.add(parameter.name)
            names.add(parameter.name)

        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"Duplicate parameter configuration for: {duplicate_list}")

        return self

    @model_validator(mode="after")
    def validate_unique_custom_kwarg_names(self) -> "PydanticOpenAPIGeneratorConfig":
        names: set[str] = set()
        duplicates: set[str] = set()
        for custom_kwarg in self.custom_kwargs:
            code_name = custom_kwarg.code_name
            if code_name in names:
                duplicates.add(code_name)
            names.add(code_name)

        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"Duplicate custom kwarg configuration for: {duplicate_list}")

        return self
