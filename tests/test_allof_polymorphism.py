"""Exercise generated discriminator families as importable Pydantic packages."""

import importlib
import json
import sys
from copy import deepcopy

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from pydantic_openapi_generator.common import Formatter, PydanticVersion
from pydantic_openapi_generator.generate_data import write_data
from pydantic_openapi_generator.language_converters.python import common, model_generator
from pydantic_openapi_generator.parsers import (
    generate_code_3_0,
    generate_code_3_1,
    parse_openapi_3_0,
    parse_openapi_3_1,
)


@pytest.fixture(params=["3.0.3", "3.1.0", "3.1.1"])
def version(request):
    return request.param


def _spec(version, schemas, paths=None):
    return {
        "openapi": version,
        "info": {"title": "Polymorphism regression", "version": "1"},
        "paths": paths or {},
        "components": {"schemas": schemas},
    }


def _parse(version, schemas):
    parser = parse_openapi_3_0 if version.startswith("3.0") else parse_openapi_3_1
    return parser(_spec(version, schemas)).components


@pytest.fixture
def build_package(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "_custom_template_path", None)
    monkeypatch.setattr(model_generator, "_POLYMORPHIC_REFERENCES", {})
    monkeypatch.setattr(model_generator, "_REFERENCE_TYPE_OVERRIDES", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    names = []

    def build(version, schemas, paths=None):
        parser = parse_openapi_3_0 if version.startswith("3.0") else parse_openapi_3_1
        converter = generate_code_3_0 if version.startswith("3.0") else generate_code_3_1
        data = converter(parser(_spec(version, schemas, paths)))
        name = f"polymorphic_runtime_{len(names)}"
        names.append(name)
        write_data(data, tmp_path / name, Formatter.NONE)
        return importlib.import_module(name), data

    yield build
    for name in names:
        for module in tuple(sys.modules):
            if module == name or module.startswith(name + "."):
                sys.modules.pop(module)


def _hierarchy():
    return {
        "Base": {
            "type": "object",
            "discriminator": {"propertyName": "kind"},
            "required": ["kind", "shared"],
            "properties": {
                "kind": {"type": "string", "enum": ["Base", "Middle", "Leaf", "Sibling"]},
                "shared": {"type": "string"},
                "next": {"$ref": "#/components/schemas/Base"},
            },
        },
        "Middle": {
            "allOf": [
                {"$ref": "#/components/schemas/Base"},
                {"required": ["name"], "properties": {"name": {"type": "string"}}},
            ]
        },
        "Leaf": {
            "allOf": [
                {"$ref": "#/components/schemas/Middle"},
                {"required": ["active"], "properties": {"active": {"type": "boolean"}}},
            ]
        },
        "Sibling": {
            "allOf": [
                {"allOf": [{"$ref": "#/components/schemas/Base"}]},
                {"properties": {"count": {"type": "integer"}}},
            ]
        },
        "Envelope": {
            "required": ["item"],
            "properties": {
                "item": {"$ref": "#/components/schemas/Base"},
                "many": {"type": "array", "items": {"$ref": "#/components/schemas/Base"}},
                "by_name": {"type": "object", "additionalProperties": {"$ref": "#/components/schemas/Base"}},
                "intermediate": {"$ref": "#/components/schemas/Middle"},
            },
        },
    }


def test_recursive_hierarchy_round_trip_and_public_aliases(version, build_package):
    package, data = build_package(version, _hierarchy())
    leaf = {"kind": "Leaf", "shared": "leaf", "name": "Alex", "active": False}
    payload = {
        "item": {**leaf, "next": {"kind": "Sibling", "shared": "nested", "count": 0}},
        "many": [{"kind": "Base", "shared": "base"}, leaf],
        "by_name": {"driver": leaf},
        "intermediate": leaf,
    }
    value = package.Envelope.model_validate(payload)
    assert isinstance(value.item, package.Leaf)
    assert isinstance(value.item.next, package.Sibling)
    assert isinstance(value.many[0], package.Base)
    assert isinstance(value.many[1], package.Leaf)
    assert isinstance(value.by_name["driver"], package.Leaf)
    assert isinstance(value.intermediate, package.Leaf)
    assert value.model_dump(mode="json", exclude_unset=True, warnings="error") == payload
    assert json.loads(value.model_dump_json(exclude_unset=True, warnings="error")) == payload
    assert isinstance(TypeAdapter(package.BaseVariant).validate_python(leaf), package.Leaf)
    assert package.Envelope.model_json_schema()["properties"]["item"]["oneOf"]
    assert {model.file_name for model in data.models} >= {"BaseVariant", "MiddleVariant"}
    # Dispatch tags do not invent restrictions on directly constructed models.
    assert package.Leaf.model_validate({**leaf, "kind": "Middle"}).kind == "Middle"


@pytest.mark.parametrize(
    "payload,error_type",
    [
        ({"shared": "missing tag"}, "union_tag_not_found"),
        ({"kind": "unknown", "shared": "invalid tag"}, "union_tag_invalid"),
        ({"kind": "Leaf", "shared": "missing inherited name", "active": False}, "missing"),
    ],
)
def test_dispatch_rejects_unknown_missing_tags_and_invalid_subtypes(version, build_package, payload, error_type):
    package, _ = build_package(version, _hierarchy())
    with pytest.raises(ValidationError) as error:
        package.Envelope.model_validate({"item": payload})
    assert any(item["type"] == error_type for item in error.value.errors())
    with pytest.raises(ValidationError):
        TypeAdapter(package.MiddleVariant).validate_python({"kind": "Sibling", "shared": "wrong family"})


def test_explicit_mapping_multiple_tags_and_singleton_family(version, build_package):
    schemas = {
        "Animal": {
            "discriminator": {"propertyName": "kind", "mapping": {"hound": "Dog", "puppy": "#/components/schemas/Dog"}},
            "required": ["kind"],
            "properties": {"kind": {"type": "string", "enum": ["hound", "puppy"]}},
        },
        "Dog": {"allOf": [{"$ref": "#/components/schemas/Animal"}], "properties": {"barks": {"type": "boolean"}}},
    }
    package, _ = build_package(version, schemas)
    adapter = TypeAdapter(package.AnimalVariant)
    for tag in ("hound", "puppy"):
        payload = {"kind": tag, "barks": False}
        dog = adapter.validate_python(payload)
        assert isinstance(dog, package.Dog)
        assert adapter.dump_python(dog, exclude_unset=True, warnings="error") == payload
    # One concrete model and one tag still checks missing/unknown discriminator values.
    singleton = deepcopy(schemas)
    singleton["Animal"]["discriminator"]["mapping"].pop("puppy")
    single, _ = build_package(version, singleton)
    adapter = TypeAdapter(single.AnimalVariant)
    assert isinstance(adapter.validate_python({"kind": "hound"}), single.Dog)
    for payload in ({}, {"kind": "puppy"}):
        with pytest.raises(ValidationError):
            adapter.validate_python(payload)


def test_nullable_wrappers_and_nested_collection_references(build_package):
    schemas = _hierarchy()
    schemas["NullableBase"] = {"anyOf": [{"$ref": "#/components/schemas/Base"}, {"type": "null"}]}
    schemas["Envelope"]["properties"].update(
        {
            "nullable": {"$ref": "#/components/schemas/NullableBase"},
            "inline_nullable": {"anyOf": [{"$ref": "#/components/schemas/Base"}, {"type": "null"}]},
            "matrix": {"type": "array", "items": {"type": "array", "items": {"$ref": "#/components/schemas/Base"}}},
        }
    )
    package, _ = build_package("3.1.1", schemas)
    leaf = {"kind": "Leaf", "shared": "leaf", "name": "Alex", "active": False}
    payload = {"item": leaf, "nullable": leaf, "inline_nullable": None, "matrix": [[leaf]]}
    result = package.Envelope.model_validate(payload)
    assert isinstance(result.nullable, package.Leaf)
    assert isinstance(result.matrix[0][0], package.Leaf)
    assert result.model_dump(exclude_unset=True) == payload


@pytest.mark.parametrize(
    "root,key",
    [
        ("Party", "party_type"),
        ("Incident", "incident_code"),
        ("Loss_Estimate_Item", "item_type"),
        ("Physical_Item", "item_type"),
        ("Purchase_Order", "purchase_order_type"),
    ],
)
def test_discriminator_families_are_generic(version, build_package, root, key):
    child = root + "Child"
    schemas = {
        root: {"discriminator": {"propertyName": key}, "required": [key], "properties": {key: {"type": "string"}}},
        child: {"allOf": [{"$ref": "#/components/schemas/" + root}], "properties": {"detail": {"type": "integer"}}},
    }
    package, _ = build_package(version, schemas)
    adapter = TypeAdapter(getattr(package, root + "Variant"))
    value = adapter.validate_python({key: child, "detail": 0})
    assert isinstance(value, getattr(package, child))
    assert adapter.dump_python(value, exclude_unset=True, warnings="error") == {key: child, "detail": 0}


@pytest.mark.parametrize(
    "problem", ["duplicate", "unrelated_mapping", "alias_collision", "missing_property", "invalid_enum"]
)
def test_ambiguous_or_invalid_hierarchies_fail_generation(version, problem):
    schemas = _hierarchy()
    if problem == "duplicate":
        schemas["Middle"]["properties"] = {"kind": {"type": "string", "enum": ["Leaf"]}}
    elif problem == "unrelated_mapping":
        schemas["Base"]["discriminator"]["mapping"] = {"outside": "Envelope"}
    elif problem == "alias_collision":
        schemas["BaseVariant"] = {"type": "object"}
    elif problem == "missing_property":
        schemas["Base"]["properties"].pop("kind")
    else:
        schemas["Base"]["properties"]["kind"]["enum"] = ["Base"]
    with pytest.raises(ValueError, match="(?i)discriminator|alias.*collision"):
        model_generator.generate_models(_parse(version, schemas))


def test_plain_allof_does_not_enable_dispatch_or_leak_previous_generation(version):
    model_generator.generate_models(_parse(version, _hierarchy()))
    schemas = _hierarchy()
    schemas["Base"].pop("discriminator")
    models = model_generator.generate_models(_parse(version, schemas))
    assert not any(model.file_name.endswith("Variant") for model in models)
    envelope = next(model for model in models if model.file_name == "Envelope")
    assert next(prop for prop in envelope.properties if prop.name == "item").type.converted_type == "Base"
    assert not any(model.defer_model_imports for model in models)


def test_v1_generation_reports_unsupported_dispatch_instead_of_invalid_code(version):
    with pytest.raises(NotImplementedError, match="Pydantic v2"):
        model_generator.generate_models(_parse(version, _hierarchy()), PydanticVersion.V1)


def test_direct_response_and_array_response_dispatch(version, build_package, monkeypatch):
    paths = {}
    for path, schema in (
        ("/item", {"$ref": "#/components/schemas/Base"}),
        ("/items", {"type": "array", "items": {"$ref": "#/components/schemas/Base"}}),
    ):
        paths[path] = {
            "get": {
                "operationId": "get" + path[1:].title(),
                "responses": {"200": {"description": "A result", "content": {"application/json": {"schema": schema}}}},
            }
        }
    package, _ = build_package(version, _hierarchy(), paths)
    leaf = {"kind": "Leaf", "shared": "leaf", "name": "Alex", "active": False}
    original = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[leaf] if request.url.path == "/items" else leaf)
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**{**kwargs, "transport": transport}))
    client = package.SyncClient(base_url="https://example.invalid", retry=None)
    assert isinstance(client.getItem(), package.Leaf)
    assert isinstance(client.getItems()[0], package.Leaf)


def test_local_single_value_enum_tags_take_precedence_over_component_names(version, build_package):
    schemas = {
        "Animal": {
            "discriminator": {"propertyName": "kind"},
            "required": ["kind"],
            "properties": {"kind": {"type": "string", "enum": ["hound"]}},
        },
        "Dog": {
            "allOf": [
                {"$ref": "#/components/schemas/Animal"},
                {"properties": {"kind": {"type": "string", "enum": ["hound"]}, "barks": {"type": "boolean"}}},
            ]
        },
    }
    package, _ = build_package(version, schemas)
    value = TypeAdapter(package.AnimalVariant).validate_python({"kind": "hound", "barks": False})
    assert isinstance(value, package.Dog)
    assert value.barks is False
    assert package.Dog.model_fields["kind"].is_required()


@pytest.mark.parametrize("conflicting", [False, True])
def test_existing_oneof_discriminators_coexist_or_report_conflicting_mappings(version, build_package, conflicting):
    schemas = _hierarchy()
    schemas["Envelope"]["properties"]["selection"] = {
        "oneOf": [{"$ref": "#/components/schemas/Leaf"}, {"$ref": "#/components/schemas/Sibling"}],
        "discriminator": {
            "propertyName": "kind",
            "mapping": {
                "other-leaf" if conflicting else "Leaf": "#/components/schemas/Leaf",
                "Sibling": "#/components/schemas/Sibling",
            },
        },
    }
    if conflicting:
        with pytest.raises(ValueError, match="Conflicting discriminator mappings"):
            build_package(version, schemas)
    else:
        package, _ = build_package(version, schemas)
        leaf = {"kind": "Leaf", "shared": "leaf", "name": "Alex", "active": False}
        value = package.Envelope.model_validate({"item": leaf, "selection": leaf})
        assert isinstance(value.selection, package.Leaf)
        assert value.model_dump(mode="json", exclude_unset=True) == {"item": leaf, "selection": leaf}


def test_generation_without_components_clears_polymorphic_reference_state(version, build_package):
    build_package(version, _hierarchy())
    document = _spec(version, {})
    document.pop("components")
    parser = parse_openapi_3_0 if version.startswith("3.0") else parse_openapi_3_1
    converter = generate_code_3_0 if version.startswith("3.0") else generate_code_3_1
    converter(parser(document))
    assert model_generator._POLYMORPHIC_REFERENCES == {}
