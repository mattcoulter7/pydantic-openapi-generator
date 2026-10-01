from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from . import common

ENUM_TEMPLATE = "enum.jinja2"
MODELS_TEMPLATE = "models.jinja2"
MODELS_TEMPLATE_PYDANTIC_V2 = "models_pydantic_2.jinja2"
ALIAS_UNION_TEMPLATE = "alias_union.jinja2"
DISCRIMINATOR_ENUM_TEMPLATE = "discriminator_enum.jinja2"
HTTP_EXCEPTION_TEMPLATE = "http_exception.jinja2"
SYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2 = "sync_client_httpx_pydantic_2.jinja2"
ASYNC_CLIENT_HTTPX_TEMPLATE_PYDANTIC_V2 = "async_client_httpx_pydantic_2.jinja2"
TEMPLATE_PATH = Path(__file__).parent / "templates"


def create_jinja_env():
    custom_template_path = common.get_custom_template_path()
    environment = Environment(
        loader=(
            ChoiceLoader(
                [
                    FileSystemLoader(custom_template_path),
                    FileSystemLoader(TEMPLATE_PATH),
                ]
            )
            if custom_template_path is not None
            else FileSystemLoader(TEMPLATE_PATH)
        ),
        autoescape=select_autoescape(default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    environment.filters["normalize_symbol"] = common.normalize_symbol
    environment.filters["python_repr"] = repr

    return environment
