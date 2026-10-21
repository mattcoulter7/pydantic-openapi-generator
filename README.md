# Pydantic OpenAPI Generator

[![PyPI](https://img.shields.io/pypi/v/pydantic-openapi-generator.svg)][pypi_]
[![Status](https://img.shields.io/pypi/status/pydantic-openapi-generator.svg)][status]
[![Python Version](https://img.shields.io/pypi/pyversions/pydantic-openapi-generator)][python version]
[![License](https://img.shields.io/pypi/l/pydantic-openapi-generator)][license]

[![](https://img.shields.io/static/v1?label=documentation&message=enabled&color=<COLOR>)][documentation]
[![Tests](https://github.com/mattcoulter7/pydantic-openapi-generator/workflows/Tests/badge.svg)][tests]
[![Codecov](https://codecov.io/gh/mattcoulter7/pydantic-openapi-generator/branch/main/graph/badge.svg)][codecov]

[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)][pre-commit]
[![Black](https://img.shields.io/badge/code%20style-black-000000.svg)][black]

[pypi_]: https://pypi.org/project/pydantic-openapi-generator/
[status]: https://pypi.org/project/pydantic-openapi-generator/
[python version]: https://pypi.org/project/pydantic-openapi-generator
[documentation]: https://github.com/mattcoulter7/pydantic-openapi-generator
[tests]: https://github.com/mattcoulter7/pydantic-openapi-generator/actions/workflows/ci.yaml
[codecov]: https://app.codecov.io/gh/mattcoulter7/pydantic-openapi-generator
[pre-commit]: https://github.com/pre-commit/pre-commit
[black]: https://github.com/psf/black

![](logo.png)

---
__Documentation:__ [here][documentation]

---

## Migration from auth-broker

As of `pydantic-openapi-generator` version `2.2.8`, this package has moved out
of the `auth-broker` organisation, been renamed, and had its import namespace
updated.

| Item | Previous | Current |
| --- | --- | --- |
| GitHub repository | [`auth-broker/openapi-python-generator`](https://github.com/auth-broker/openapi-python-generator) | [`mattcoulter7/pydantic-openapi-generator`](https://github.com/mattcoulter7/pydantic-openapi-generator) |
| PyPI package | [`ab-openapi-python-generator`](https://pypi.org/project/ab-openapi-python-generator/) | [`pydantic-openapi-generator`](https://pypi.org/project/pydantic-openapi-generator/) |
| CLI command | `ab-openapi-python-generator` | `pydantic-openapi-generator` |
| Import namespace | `ab_openapi_python_generator` | `pydantic_openapi_generator` |

The old PyPI package is retained as an archived historical package. New work
should use `pydantic-openapi-generator` and `pydantic_openapi_generator`.

## Features

- __Ease of use__. Provide input, output and the library, and the generator will do the rest.
- __Type safety and type hinting.__ __Pydantic OpenAPI Generator__ makes heavy use of pydantic models to provide type-safe data structures.
- __Support for multiple rest frameworks.__ __Pydantic OpenAPI Generator__ currently supports the following:
    - [httpx](https://pypi.org/project/httpx/)
    - [requests](https://pypi.org/project/requests/)
    - [aiohttp](https://pypi.org/project/aiohttp/)
- __Async and sync code generation support__, depending on the framework. It will automatically create both for frameworks that support both.
- __Easily extendable using Jinja2 templates__. The code is designed to be easily extendable and should support even more languages and frameworks in the future.
- __Fully tested__. Every generated code is automatically tested against the OpenAPI spec and we have 100% coverage.
- __Usage as CLI or as library__.

## Requirements

- Python 3.12+

## Installation

You can install _Pydantic OpenAPI Generator_ via [pip] from [PyPI]:

```console
$ pip install pydantic-openapi-generator
```

## Usage

Please see the [Quick start page] for details.

### HTTP client configuration

Generated `httpx` clients default to a 30 second timeout. You can override that
and enable an `httpx-retries` policy from the generator configuration file:

```yaml
default_timeout: 30.0
retry:
  total: 3
  backoff_factor: 0.5
  status_forcelist:
    - 429
    - 502
    - 503
    - 504
```

When `retry` is configured, the generated client project must include
`httpx-retries`. Retry settings use the same Pydantic `RetryFactory` model in the
generator configuration and the generated package's `utils/retry.py`.

Clients also accept retry settings directly from JSON:

```python
client = AsyncClient.model_validate_json('{"timeout": 30.0, "retry": {"total": 3, "backoff_factor": 0.5}}')
```

`RetryFactory.get_retry()` constructs the `httpx-retries` policy, and
`get_transport()` constructs its transport. Set `retry` to `null` to disable retries.

### Polymorphic models

For Pydantic v2, an object schema with a discriminator and `allOf` descendants
generates a reusable alias such as `PartyVariant`. References to that schema,
including response bodies, array items, dictionary values, and nullable wrappers,
use the alias to select the concrete model during validation. Transitive
descendants and intermediate ancestors are supported, and recursive references
are resolved after the generated models load.

Explicit discriminator mappings take precedence over local `const` or `enum`
tags and implicit component names. Base variants are included when their tags
are permitted by the schema. Unknown or missing tags fail validation; ambiguous
mappings and generated alias name collisions fail generation.

The aliases use Pydantic callable discriminators with tagged choices. Concrete
model fields retain their declared constraints: an inferred dispatch tag does
not introduce a new `Literal` restriction on direct model construction. Ordinary
`allOf` schemas without a discriminator continue to generate flattened models.
This automatic dispatch requires Pydantic v2; v1 generation reports an explicit
unsupported-feature error for these hierarchies.

## Roadmap

- Support for all commonly used http libraries in the python ecosystem (~~requests~~, urllib, ...)
- Support for multiple languages
- Support for multiple authentication schemes
- Support custom themes

## Contributing

Contributions are very welcome.
To learn more, see the [Contributor Guide].

## License

Distributed under the terms of the [MIT license][license],
_Openapi Python Generator_ is free and open source software.

## Issues

If you encounter any problems,
please [file an issue] along with a detailed description.

## Credits

Special thanks to the peeps from [openapi-schema-pydantic](https://github.com/kuimono/openapi-schema-pydantic),
which already did a lot of the legwork by providing a pydantic schema for the OpenAPI 3.0.0+ specification.

This project was generated from [@cjolowicz]'s [Hypermodern Python Cookiecutter] template.

[@cjolowicz]: https://github.com/cjolowicz
[pypi]: https://pypi.org/
[hypermodern python cookiecutter]: https://github.com/cjolowicz/cookiecutter-hypermodern-python
[file an issue]: https://github.com/mattcoulter7/pydantic-openapi-generator/issues
[pip]: https://pip.pypa.io/

<!-- github-only -->

[license]: https://github.com/mattcoulter7/pydantic-openapi-generator/blob/main/LICENSE
[contributor guide]: https://github.com/mattcoulter7/pydantic-openapi-generator/blob/main/CONTRIBUTING.md
[Quick start page]: https://github.com/mattcoulter7/pydantic-openapi-generator#usage
