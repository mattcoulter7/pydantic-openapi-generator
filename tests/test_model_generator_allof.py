"""Regression coverage for flattened OpenAPI allOf object models."""

import importlib
import sys

import pytest
from pydantic import ValidationError

from pydantic_openapi_generator.language_converters.python import model_generator
from pydantic_openapi_generator.parsers import parse_openapi_3_0, parse_openapi_3_1


@pytest.fixture(params=["3.0.3", "3.1.0", "3.1.1"])
def openapi_version(request):
    return request.param


def _components(version, schemas):
    parser = parse_openapi_3_0 if version.startswith("3.0") else parse_openapi_3_1
    return parser(
        {
            "openapi": version,
            "info": {"title": "allOf regression", "version": "1.0"},
            "paths": {},
            "components": {"schemas": schemas},
        }
    ).components


def _hierarchy():
    return {
        "Contact": {
            "type": "object",
            "required": ["email"],
            "properties": {"email": {"type": "string"}},
        },
        "Party": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "notes": {"type": "string", "default": "unspecified"},
                "contact": {"$ref": "#/components/schemas/Contact"},
            },
        },
        "Person": {
            "allOf": [
                {"$ref": "#/components/schemas/Party"},
                {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            ]
        },
        "Driver": {
            "description": "A driver with all inherited party and person fields.",
            "allOf": [
                {"$ref": "#/components/schemas/Person"},
                {
                    "allOf": [
                        {
                            "type": "object",
                            "required": ["licence_number"],
                            "properties": {"licence_number": {"type": "string"}},
                        }
                    ]
                },
            ],
            "required": ["active"],
            "properties": {"active": {"type": "boolean"}},
        },
        "DriverCopy": {"allOf": [{"$ref": "#/components/schemas/Driver"}]},
    }


def test_allof_collects_nested_inherited_and_top_level_fields(openapi_version):
    components = _components(openapi_version, _hierarchy())
    original_driver = components.schemas["Driver"].model_dump()
    models = {model.file_name: model for model in model_generator.generate_models(components)}

    assert {prop.name for prop in models["Person"].properties} == {"id", "notes", "contact", "name"}
    expected_fields = {"id", "notes", "contact", "name", "licence_number", "active"}
    for name in ("Driver", "DriverCopy"):
        model = models[name]
        fields = {prop.name: prop for prop in model.properties}
        assert set(fields) == expected_fields
        assert {prop.name for prop in model.properties if prop.required} == {"id", "name", "licence_number", "active"}
        assert fields["notes"].default == repr("unspecified")
        assert fields["notes"].type.converted_type == "Optional[str]"
        assert fields["contact"].default == "None"
        assert fields["contact"].type.converted_type == "Optional[Contact]"
        assert fields["contact"].type.import_types == ["from .Contact import Contact"]
        compile(model.content, f"<generated {name}>", "exec")

    assert original_driver == components.schemas["Driver"].model_dump()
    assert models["Driver"].openapi_object.description in models["Driver"].content


def test_generated_allof_package_validates_and_round_trips(openapi_version, tmp_path, monkeypatch):
    models = model_generator.generate_models(_components(openapi_version, _hierarchy()))
    package_name = "allof_runtime_" + openapi_version.replace(".", "_")
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for model in models:
        (package / f"{model.file_name}.py").write_text(model.content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        driver_type = importlib.import_module(f"{package_name}.Driver").Driver
        copy_type = importlib.import_module(f"{package_name}.DriverCopy").DriverCopy
        payload = {
            "id": "party-1",
            "name": "Alex",
            "licence_number": "12345",
            "active": True,
            "notes": "verified",
            "contact": {"email": "alex@example.com"},
        }
        for model_type in (driver_type, copy_type):
            model_type.model_rebuild()
            assert model_type.model_validate(payload).model_dump() == payload
            required_payload = {key: payload[key] for key in ("id", "name", "licence_number", "active")}
            instance = model_type.model_validate(required_payload)
            assert instance.notes == "unspecified"
            assert instance.contact is None

            for required_field in required_payload:
                missing = {key: value for key, value in required_payload.items() if key != required_field}
                with pytest.raises(ValidationError) as exc:
                    model_type.model_validate(missing)
                assert any(
                    error["loc"] == (required_field,) and error["type"] == "missing" for error in exc.value.errors()
                )
    finally:
        for module_name in tuple(sys.modules):
            if module_name == package_name or module_name.startswith(package_name + "."):
                sys.modules.pop(module_name)


def test_allof_deduplicates_identical_properties_and_unions_required(openapi_version):
    components = _components(
        openapi_version,
        {
            "Parent": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
            "Child": {
                "allOf": [
                    {"$ref": "#/components/schemas/Parent"},
                    {
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}},
                    },
                ]
            },
        },
    )
    child = next(model for model in model_generator.generate_models(components) if model.file_name == "Child")
    assert len(child.properties) == 1
    assert child.properties[0].name == "id"
    assert child.properties[0].required


def test_allof_intersects_compatible_enum_properties(openapi_version):
    components = _components(
        openapi_version,
        {
            "Child": {
                "allOf": [
                    {"properties": {"status": {"type": "string", "enum": ["pending", "approved"]}}},
                    {"properties": {"status": {"type": "string", "enum": ["approved", "denied"]}}},
                ]
            }
        },
    )
    child = model_generator.generate_models(components)[0]
    assert len(child.properties) == 1
    assert child.properties[0].type.converted_type == "Optional[Literal['approved']]"
    assert not child.properties[0].required
    assert child.properties[0].default == "None"
    namespace = {}
    exec(compile(child.content, "<generated Child>", "exec"), namespace)
    child_type = namespace["Child"]
    assert child_type.model_validate({}).status is None
    assert child_type.model_validate({"status": "approved"}).status == "approved"
    with pytest.raises(ValidationError):
        child_type.model_validate({"status": "pending"})


def test_allof_combines_compatible_bounds_at_runtime(openapi_version):
    components = _components(
        openapi_version,
        {
            "Child": {
                "allOf": [
                    {"properties": {"value": {"type": "integer", "minimum": 0, "maximum": 100}}},
                    {"properties": {"value": {"type": "integer", "minimum": 10, "maximum": 50}}},
                ]
            }
        },
    )
    child = model_generator.generate_models(components)[0]
    namespace = {}
    exec(compile(child.content, "<generated Child>", "exec"), namespace)
    child_type = namespace["Child"]
    assert child_type.model_validate({"value": 10}).value == 10
    assert child_type.model_validate({"value": 50}).value == 50
    for value in (9, 51):
        with pytest.raises(ValidationError):
            child_type.model_validate({"value": value})


def test_allof_number_intersections_keep_fractional_values(openapi_version):
    components = _components(
        openapi_version,
        {
            "Child": {
                "allOf": [
                    {"properties": {"value": {"type": "number", "minimum": 0, "maximum": 100}}},
                    {"properties": {"value": {"type": "number", "minimum": 1, "maximum": 3}}},
                ]
            }
        },
    )
    child = model_generator.generate_models(components)[0]
    namespace = {}
    exec(compile(child.content, "<generated Child>", "exec"), namespace)
    assert namespace["Child"].model_validate({"value": 1.5}).value == 1.5


@pytest.mark.parametrize("version", ["3.1.0", "3.1.1"])
@pytest.mark.parametrize("const_first", [True, False])
def test_allof_const_and_enum_intersection_retains_const(version, const_first):
    definitions = [
        {"properties": {"value": {"type": "string", "const": "a"}}},
        {"properties": {"value": {"type": "string", "enum": ["a", "b"]}}},
    ]
    components = _components(version, {"Child": {"allOf": definitions if const_first else definitions[::-1]}})
    child = model_generator.generate_models(components)[0]
    namespace = {}
    exec(compile(child.content, "<generated Child>", "exec"), namespace)
    child_type = namespace["Child"]
    assert child_type.model_validate({"value": "a"}).value == "a"
    with pytest.raises(ValidationError):
        child_type.model_validate({"value": "b"})


def test_allof_allows_metadata_differences_on_duplicate_properties(openapi_version):
    components = _components(
        openapi_version,
        {
            "Child": {
                "allOf": [
                    {"properties": {"value": {"type": "string", "description": "Parent description"}}},
                    {"properties": {"value": {"type": "string", "description": "Child description"}}},
                ]
            }
        },
    )
    child = model_generator.generate_models(components)[0]
    assert len(child.properties) == 1
    assert child.properties[0].type.converted_type == "Optional[str]"


@pytest.mark.parametrize(
    "left,right",
    [
        ({"type": "string"}, {"type": "integer"}),
        ({"type": "string", "enum": ["a"]}, {"type": "string", "enum": ["b"]}),
        ({"type": "number", "minimum": 10}, {"type": "number", "maximum": 5}),
    ],
)
def test_allof_rejects_incompatible_duplicate_properties(openapi_version, left, right):
    components = _components(
        openapi_version,
        {"Child": {"allOf": [{"properties": {"value": left}}, {"properties": {"value": right}}]}},
    )
    with pytest.raises(ValueError, match="(?i)conflict|incompatible|intersection"):
        model_generator.generate_models(components)


def test_allof_checks_top_level_properties_for_conflicts(openapi_version):
    components = _components(
        openapi_version,
        {
            "Parent": {"properties": {"value": {"type": "string"}}},
            "Child": {
                "allOf": [{"$ref": "#/components/schemas/Parent"}],
                "properties": {"value": {"type": "integer"}},
            },
        },
    )
    with pytest.raises(ValueError, match="(?i)conflict|incompatible|intersection"):
        model_generator.generate_models(components)


@pytest.mark.parametrize(
    "schemas,error",
    [
        ({"A": {"allOf": [{"$ref": "#/components/schemas/A"}]}}, "Circular allOf"),
        (
            {
                "A": {"allOf": [{"$ref": "#/components/schemas/B"}]},
                "B": {"allOf": [{"$ref": "#/components/schemas/A"}]},
            },
            "Circular allOf",
        ),
        ({"A": {"allOf": [{"$ref": "#/components/schemas/Missing"}]}}, "Unresolved allOf"),
        ({"A": {"allOf": [{"$ref": "other.yaml#/components/schemas/Parent"}]}}, "Unsupported allOf reference"),
    ],
)
def test_allof_reports_invalid_composition_references(openapi_version, schemas, error):
    with pytest.raises(ValueError, match=error):
        model_generator.generate_models(_components(openapi_version, schemas))


def test_allof_decodes_json_pointer_component_names(openapi_version):
    components = _components(
        openapi_version,
        {
            "Parent/with~marker": {"properties": {"id": {"type": "string"}}},
            "Child": {"allOf": [{"$ref": "#/components/schemas/Parent~1with~0marker"}]},
        },
    )
    child = next(model for model in model_generator.generate_models(components) if model.file_name == "Child")
    assert [prop.name for prop in child.properties] == ["id"]


def test_allof_preserves_recursive_properties_without_composition_cycle(openapi_version):
    components = _components(
        openapi_version,
        {
            "Node": {
                "properties": {
                    "id": {"type": "string"},
                    "next": {"$ref": "#/components/schemas/Node"},
                }
            },
            "NamedNode": {
                "allOf": [{"$ref": "#/components/schemas/Node"}],
                "properties": {"name": {"type": "string"}},
            },
        },
    )
    models = {model.file_name: model for model in model_generator.generate_models(components)}
    fields = {prop.name: prop for prop in models["NamedNode"].properties}
    assert set(fields) == {"id", "next", "name"}
    assert fields["next"].type.converted_type == "Optional[Node]"
    assert fields["next"].type.import_types == ["from .Node import Node"]


def test_allof_inherits_discriminator_field_without_inventing_subtype_literal(openapi_version):
    components = _components(
        openapi_version,
        {
            "Party": {
                "discriminator": {"propertyName": "party_type"},
                "required": ["party_type"],
                "properties": {"party_type": {"type": "string", "enum": ["person", "business"]}},
            },
            "Driver": {"allOf": [{"$ref": "#/components/schemas/Party"}]},
        },
    )
    models = {model.file_name: model for model in model_generator.generate_models(components)}
    inherited_field = models["Driver"].properties[0]
    assert inherited_field.name == "party_type"
    assert inherited_field.required
    assert inherited_field.type == models["Party"].properties[0].type
    assert "Literal" not in inherited_field.type.converted_type


def test_inline_allof_fails_instead_of_generating_tuple(openapi_version):
    components = _components(
        openapi_version,
        {
            "Parent": {"properties": {"id": {"type": "string"}}},
            "Container": {
                "properties": {
                    "composed": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Parent"},
                            {"properties": {"name": {"type": "string"}}},
                        ]
                    }
                }
            },
        },
    )
    with pytest.raises(NotImplementedError, match="(?i)inline.*allof|allof.*inline"):
        model_generator.generate_models(components)


@pytest.mark.parametrize(
    "member",
    [
        {"type": "string", "minLength": 2},
        {"type": "array", "items": {"type": "string"}},
        {"type": "object", "additionalProperties": False},
    ],
)
def test_named_allof_rejects_unsupported_assertions_instead_of_empty_model(openapi_version, member):
    components = _components(openapi_version, {"Composed": {"allOf": [member]}})
    with pytest.raises(NotImplementedError, match="(?i)object.*allof|allof.*object"):
        model_generator.generate_models(components)
