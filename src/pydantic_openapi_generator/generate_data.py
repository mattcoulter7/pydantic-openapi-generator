from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

import black
import click
import httpx
import isort
import orjson
import yaml  # type: ignore
from black.report import NothingChanged  # type: ignore
from httpx import ConnectError, ConnectTimeout
from pydantic import ValidationError

from .common import FormatOptions, Formatter, HTTPLibrary, PydanticVersion
from .config.generator_config import PydanticOpenAPIGeneratorConfig
from .config.loader import load_config
from .models import ConversionResult
from .parsers import (
    generate_code_3_0,
    generate_code_3_1,
    parse_openapi_3_0,
    parse_openapi_3_1,
)
from .version_detector import detect_openapi_version

DELETE_MARKER = "x-pydantic-openapi-generator-delete"


def write_code(path: Path, content: str, formatter: Formatter) -> None:
    """
    Write the content to the file at the given path.
    :param path: The path to the file.
    :param content: The content to write.
    :param formatter: The formatter applied to the code written.
    """
    if formatter == Formatter.BLACK:
        formatted_contend = format_using_black(content)
    elif formatter == Formatter.NONE:
        formatted_contend = content
    else:
        raise NotImplementedError(f"Missing implementation for formatter {formatter!r}.")
    with open(path, "w") as f:
        f.write(formatted_contend)


def format_using_black(content: str) -> str:
    try:
        formatted_contend = black.format_file_contents(
            content,
            fast=FormatOptions.skip_validation,
            mode=black.FileMode(line_length=FormatOptions.line_length),
        )
    except NothingChanged:
        return content
    return isort.code(formatted_contend, line_length=FormatOptions.line_length)


def load_openapi_data(source: Union[str, Path]) -> dict[str, Any]:
    """
    Load an OpenAPI JSON/YAML document from a URL or local file path.
    """
    if not isinstance(source, Path) and (source.startswith("http://") or source.startswith("https://")):
        content = httpx.get(source).text
    else:
        with open(source, "r") as f:
            content = f.read()

    try:
        data = orjson.loads(content)
    except orjson.JSONDecodeError:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            click.echo(f"File {source} is neither a valid JSON nor YAML file: {str(e)}")
            raise

    if not isinstance(data, dict):
        raise ValueError(f"OpenAPI data loaded from {source} must be a JSON/YAML object.")

    return data


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge two dictionaries. Overlay lists and scalar values replace base values.
    A value of {"x-pydantic-openapi-generator-delete": true} removes the key.
    """
    result = base.copy()
    for key, overlay_value in overlay.items():
        if overlay_value == {DELETE_MARKER: True}:
            result.pop(key, None)
            continue

        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            result[key] = deep_merge(base_value, overlay_value)
        else:
            result[key] = overlay_value
    return result


def get_open_api(source: Union[str, Path], overlay_paths: Optional[Sequence[Union[str, Path]]] = None):
    """
    Tries to fetch the openapi specification file from the web or load from a local file.
    Supports both JSON and YAML formats. Returns the according OpenAPI object.
    Automatically supports OpenAPI 3.0 and 3.1 specifications with intelligent version detection.

    Args:
        source: URL or file path to the OpenAPI specification
        overlay_paths: Optional JSON/YAML overlay files to deep-merge into source in order

    Returns:
        tuple: (OpenAPI object, version) where version is "3.0" or "3.1"

    Raises:
        FileNotFoundError: If the specified file cannot be found
        ConnectError: If the URL cannot be accessed
        ValidationError: If the specification is invalid
        JSONDecodeError/YAMLError: If the file cannot be parsed
    """
    try:
        data = load_openapi_data(source)
        for overlay_path in overlay_paths or ():
            data = deep_merge(data, load_openapi_data(overlay_path))

        # Detect version and parse with appropriate parser
        version = detect_openapi_version(data)

        if version == "3.0":
            openapi_obj = parse_openapi_3_0(data)  # type: ignore[assignment]
        elif version == "3.1":
            openapi_obj = parse_openapi_3_1(data)  # type: ignore[assignment]
        else:
            # Unsupported version detected (version detection already limited to 3.0 / 3.1)
            raise ValueError(f"Unsupported OpenAPI version: {version}. Only 3.0.x and 3.1.x are supported.")

        return openapi_obj, version

    except FileNotFoundError:
        click.echo(f"File {source} not found. Please make sure to pass the path to the OpenAPI specification.")
        raise
    except (ConnectError, ConnectTimeout):
        click.echo(f"Could not connect to {source}.")
        raise ConnectError(f"Could not connect to {source}.") from None
    except ValidationError:
        click.echo(f"File {source} is not a valid OpenAPI 3.0+ specification.")
        raise


def write_data(data: ConversionResult, output: Union[str, Path], formatter: Formatter) -> None:
    """
    Write generated code to disk.

    Creates:
      - models/ (and models/__init__.py)
      - clients/ (and clients/__init__.py)
      - exceptions (package root/__init__.py)
      - __init__.py (package root)
    """
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    # ----------------------------
    # models/
    # ----------------------------
    models_path = out / "models"
    models_path.mkdir(parents=True, exist_ok=True)

    model_files: List[str] = []
    for model in data.models:
        model_files.append(model.file_name)
        write_code(models_path / f"{model.file_name}.py", model.content, formatter)

    write_code(
        models_path / "__init__.py",
        "\n".join([f"from .{f} import *" for f in model_files]) + ("\n" if model_files else ""),
        formatter,
    )

    # ----------------------------
    # clients/
    # ----------------------------
    clients_path = out / "clients"
    clients_path.mkdir(parents=True, exist_ok=True)

    client_files: List[str] = []
    for client in data.clients:
        client_files.append(client.file_name)
        write_code(clients_path / f"{client.file_name}.py", client.content, formatter)

    write_code(
        clients_path / "__init__.py",
        "\n".join([f"from .{f} import *" for f in client_files]) + ("\n" if client_files else ""),
        formatter,
    )

    # ----------------------------
    # exceptions/
    # ----------------------------
    exceptions_path = out / "exceptions"
    exceptions_path.mkdir(parents=True, exist_ok=True)

    exception_files: List[str] = []
    for ex in data.exceptions:
        exception_files.append(ex.file_name)
        write_code(exceptions_path / f"{ex.file_name}.py", ex.content, formatter)

    write_code(
        exceptions_path / "__init__.py",
        "\n".join([f"from .{f} import *" for f in exception_files]) + ("\n" if exception_files else ""),
        formatter,
    )

    # ----------------------------
    # package __init__.py (root)
    # ----------------------------
    init_lines: List[str] = [
        "from .models import *",
        "from .clients import *",
        "from .exceptions import *",
    ]

    write_code(out / "__init__.py", "\n".join(init_lines) + "\n", formatter)


def generate_data(
    source: Union[str, Path],
    output: Union[str, Path],
    library: HTTPLibrary = HTTPLibrary.httpx,
    env_token_name: Optional[str] = None,
    use_orjson: bool = False,
    custom_template_path: Optional[str] = None,
    pydantic_version: PydanticVersion = PydanticVersion.V2,
    formatter: Formatter = Formatter.BLACK,
    config_path: Optional[Union[str, Path]] = None,
    overlay_paths: Optional[Sequence[Union[str, Path]]] = None,
    config: Optional[PydanticOpenAPIGeneratorConfig] = None,
) -> None:
    """
    Generate Python code from an OpenAPI 3.0+ specification.
    """
    openapi_obj, version = get_open_api(source, overlay_paths)
    loaded_config = config if config is not None else load_config(config_path)
    click.echo(f"Generating data from {source} (OpenAPI {version})")

    # Use version-specific generator
    if version == "3.0":
        result = generate_code_3_0(
            openapi_obj,  # type: ignore
            library,
            env_token_name,
            use_orjson,
            custom_template_path,
            pydantic_version,
            loaded_config,
        )
    elif version == "3.1":
        result = generate_code_3_1(
            openapi_obj,  # type: ignore
            library,
            env_token_name,
            use_orjson,
            custom_template_path,
            pydantic_version,
            loaded_config,
        )
    else:
        raise ValueError(f"Unsupported OpenAPI version: {version}")

    write_data(result, output, formatter)
