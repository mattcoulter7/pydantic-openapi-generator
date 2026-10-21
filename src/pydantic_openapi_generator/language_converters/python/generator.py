from typing import Optional, Union

from openapi_pydantic.v3.v3_0 import OpenAPI as OpenAPI30
from openapi_pydantic.v3.v3_1 import OpenAPI as OpenAPI31

from pydantic_openapi_generator.common import PydanticVersion
from pydantic_openapi_generator.config.generator_config import (
    PydanticOpenAPIGeneratorConfig,
)
from pydantic_openapi_generator.language_converters.python import common
from pydantic_openapi_generator.language_converters.python.client_generator import (
    generate_clients,
)
from pydantic_openapi_generator.language_converters.python.exception_generator import (
    generate_exceptions,
)
from pydantic_openapi_generator.language_converters.python.model_generator import (
    generate_models,
    generate_response_union_alias_models,
    reset_reference_overrides,
)
from pydantic_openapi_generator.models import ConversionResult, LibraryConfig

# Type alias for both OpenAPI versions
OpenAPISpec = Union[OpenAPI30, OpenAPI31]


def generator(
    data: OpenAPISpec,
    library_config: LibraryConfig,
    env_token_name: Optional[str] = None,
    use_orjson: bool = False,
    custom_template_path: Optional[str] = None,
    pydantic_version: PydanticVersion = PydanticVersion.V2,
    config: Optional[PydanticOpenAPIGeneratorConfig] = None,
) -> ConversionResult:
    """
    Generate Python code from an OpenAPI 3.0+ specification.
    """

    common.set_use_orjson(use_orjson)
    common.set_custom_template_path(custom_template_path)
    reset_reference_overrides()

    if data.components is not None:
        models = generate_models(data.components, pydantic_version)
    else:
        models = []

    # Generate response union alias models (e.g. AuthorizeResponse) from paths
    # so client return types that reference those aliases have corresponding
    # model modules available in the output.
    if data.paths is not None:
        resp_alias_models = generate_response_union_alias_models(data.paths, pydantic_version)
        if resp_alias_models:
            models.extend(resp_alias_models)

    if data.paths is not None:
        clients = generate_clients(data, data.paths, library_config, env_token_name, pydantic_version, config)
    else:
        clients = []

    return ConversionResult(
        models=models,
        clients=clients,
        exceptions=generate_exceptions(),
    )
