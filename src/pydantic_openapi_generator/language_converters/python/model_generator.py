from __future__ import annotations

import itertools
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Union

import click
from openapi_pydantic.v3 import Operation, PathItem
from openapi_pydantic.v3.v3_0 import (
    Components as Components30,
)
from openapi_pydantic.v3.v3_0 import (
    Reference as Reference30,
)
from openapi_pydantic.v3.v3_0 import Response as Response30
from openapi_pydantic.v3.v3_0 import (
    Schema as Schema30,
)
from openapi_pydantic.v3.v3_1 import (
    Components as Components31,
)
from openapi_pydantic.v3.v3_1 import (
    Reference as Reference31,
)
from openapi_pydantic.v3.v3_1 import Response as Response31
from openapi_pydantic.v3.v3_1 import (
    Schema as Schema31,
)

from pydantic_openapi_generator.common import PydanticVersion
from pydantic_openapi_generator.language_converters.python import common
from pydantic_openapi_generator.language_converters.python.jinja_config import (
    ALIAS_UNION_TEMPLATE,
    DISCRIMINATOR_ENUM_TEMPLATE,
    ENUM_TEMPLATE,
    MODELS_TEMPLATE,
    MODELS_TEMPLATE_PYDANTIC_V2,
    create_jinja_env,
)
from pydantic_openapi_generator.models import Model, Property, TypeConversion

# Type aliases for compatibility
Schema = Union[Schema30, Schema31]
Reference = Union[Reference30, Reference31]
Components = Union[Components30, Components31]


# Map of wrapper component name -> TypeConversion to use instead of generating wrapper module
_REFERENCE_TYPE_OVERRIDES: dict[str, TypeConversion] = {}


@dataclass
class ResolvedSchema:
    properties: dict[str, Schema | Reference] = field(default_factory=dict)
    required: set[str] = field(default_factory=set)
    intersected_properties: set[str] = field(default_factory=set)


_SCHEMA_ANNOTATIONS = {"title", "description", "default", "example", "examples", "deprecated", "externalDocs", "xml"}
_LOWER_LIMITS = {"minimum", "minLength", "minItems"}
_UPPER_LIMITS = {"maximum", "maxLength", "maxItems"}


def _merge_allof_property(left: Schema | Reference, right: Schema | Reference, path: str) -> Schema | Reference:
    """Intersect supported property assertions; fail instead of discarding constraints."""
    for definition in (left, right):
        if "const" in definition.model_fields_set and getattr(definition, "const", None) is None:
            raise ValueError(f"Conflicting allOf property {path}: unsupported null const intersection")
    if left == right and isinstance(left, (Reference30, Reference31)):
        return left
    if not isinstance(left, (Schema30, Schema31)) or not isinstance(right, (Schema30, Schema31)):
        raise ValueError(f"Conflicting allOf property {path}: incompatible references or schemas")

    merged = left.model_dump(exclude_none=True)
    for key, value in right.model_dump(exclude_none=True).items():
        if key in _SCHEMA_ANNOTATIONS:
            merged[key] = value
        elif key in _LOWER_LIMITS:
            merged[key] = max(merged.get(key, value), value)
        elif key in _UPPER_LIMITS:
            merged[key] = min(merged.get(key, value), value)
        elif key == "enum":
            if key in merged:
                merged[key] = [item for item in merged[key] if any(type(item) is type(v) and item == v for v in value)]
                if not merged[key]:
                    raise ValueError(f"Conflicting allOf property {path}: empty enum intersection")
            else:
                merged[key] = value
        elif key == "type" and merged.get(key) in ("integer", "number") and value in ("integer", "number"):
            merged[key] = "integer" if "integer" in (merged[key], value) else "number"
        elif key in {"type", "const"} and key not in merged:
            merged[key] = value
        elif key == "const" and type(merged[key]) is not type(value):
            raise ValueError(f"Conflicting allOf property {path}: unsupported const intersection")
        elif key not in merged or merged[key] != value:
            raise ValueError(f"Conflicting allOf property {path}: unsupported intersection for {key}")

    # Check both sides: an unsupported assertion must not slip through simply
    # because it appeared in the first definition rather than the second.
    supported = _SCHEMA_ANNOTATIONS | _LOWER_LIMITS | _UPPER_LIMITS | {"type", "enum", "const"}
    for key in left.model_dump(exclude_none=True):
        if key not in supported and getattr(right, key, None) != getattr(left, key, None):
            raise ValueError(f"Conflicting allOf property {path}: unsupported intersection for {key}")

    for lower, upper in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        if lower in merged and upper in merged and merged[lower] > merged[upper]:
            raise ValueError(f"Conflicting allOf property {path}: {lower} exceeds {upper}")
    if (
        "const" in merged
        and "enum" in merged
        and not any(type(merged["const"]) is type(value) and merged["const"] == value for value in merged["enum"])
    ):
        raise ValueError(f"Conflicting allOf property {path}: const is outside enum")
    literal_values = [merged["const"]] if "const" in merged else merged.get("enum", [])
    schema_types = merged.get("type", [])
    if not isinstance(schema_types, list):
        schema_types = [schema_types]
    literal_types = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    for value in literal_values:
        if isinstance(value, (dict, list)) or (
            schema_types and not any(type(value) in literal_types.get(kind, ()) for kind in schema_types)
        ):
            raise ValueError(f"Conflicting allOf property {path}: unsupported or incompatible literal type")
    return type(left).model_validate(merged)


def _resolve_allof_schema(
    schema: Schema,
    schemas: Mapping[str, Schema | Reference],
    *,
    stack: tuple[str, ...] = (),
    in_allof: bool = False,
) -> ResolvedSchema:
    """Collect object fields across allOf without following ordinary field references."""
    result = ResolvedSchema()

    def merge(part: ResolvedSchema) -> None:
        result.intersected_properties.update(part.intersected_properties)
        for prop_name, prop_schema in part.properties.items():
            if prop_name in result.properties:
                result.properties[prop_name] = _merge_allof_property(
                    result.properties[prop_name], prop_schema, ".".join((*stack, prop_name))
                )
                result.intersected_properties.add(prop_name)
            else:
                result.properties[prop_name] = prop_schema
        result.required.update(part.required)

    for member in schema.allOf or []:
        if isinstance(member, (Reference30, Reference31)):
            prefix = "#/components/schemas/"
            if not member.ref.startswith(prefix) or "/" in member.ref[len(prefix) :]:
                raise ValueError(f"Unsupported allOf reference: {member.ref}")
            name = member.ref[len(prefix) :].replace("~1", "/").replace("~0", "~")
            if name in stack:
                raise ValueError("Circular allOf composition: " + " -> ".join((*stack, name)))
            target = schemas.get(name)
            if not isinstance(target, (Schema30, Schema31)):
                raise ValueError(f"Unresolved allOf schema: {member.ref}")
            merge(_resolve_allof_schema(target, schemas, stack=(*stack, name), in_allof=True))
        elif isinstance(member, (Schema30, Schema31)):
            merge(_resolve_allof_schema(member, schemas, stack=stack, in_allof=True))
        else:
            raise TypeError(f"Unsupported allOf member: {type(member)!r}")

    # Non-object intersections need a different representation. Never turn them
    # into an empty BaseModel while pretending their assertions were preserved.
    if schema.allOf is not None or in_allof:
        supported = _SCHEMA_ANNOTATIONS | {
            "type",
            "properties",
            "required",
            "allOf",
            "discriminator",
            "readOnly",
            "writeOnly",
        }
        unsupported = schema.model_dump(exclude_none=True).keys() - supported
        if schema.type not in (None, "object") or unsupported:
            raise NotImplementedError("Only object allOf composition is supported for named models")
    merge(ResolvedSchema(properties=schema.properties or {}, required=set(schema.required or [])))
    return result


def _constrain_allof_property(prop: Property, schema: Schema) -> None:
    """Render the supported duplicate-property intersection in existing templates."""
    const = getattr(schema, "const", None)
    values = [const] if const is not None else getattr(schema, "enum", None)
    if values:
        literal = "Literal[" + ", ".join(repr(value) for value in values) + "]"
        prop.type.converted_type = literal if prop.required else f"Optional[{literal}]"
        prop.type.import_types = _dedupe_imports((prop.type.import_types or []) + ["from typing import Literal"])

    constraints = {}
    for key, field_name in (
        ("minimum", "ge"),
        ("maximum", "le"),
        ("minLength", "min_length"),
        ("maxLength", "max_length"),
        ("minItems", "min_length"),
        ("maxItems", "max_length"),
        ("multipleOf", "multiple_of"),
        ("pattern", "pattern"),
    ):
        value = getattr(schema, key, None)
        if value is not None:
            constraints[field_name] = value
    for key, field_name, inclusive in (("exclusiveMinimum", "gt", "ge"), ("exclusiveMaximum", "lt", "le")):
        value = getattr(schema, key, None)
        if isinstance(value, bool):
            if value and inclusive in constraints:
                constraints[field_name] = constraints.pop(inclusive)
        elif value is not None:
            constraints[field_name] = value
    if constraints:
        args = ", ".join(f"{key}={value!r}" for key, value in constraints.items())
        prop.type.converted_type = f"Annotated[{prop.type.converted_type}, Field({args})]"
        prop.type.import_types = _dedupe_imports((prop.type.import_types or []) + ["from typing import Annotated"])


def _is_null_schema(s: object) -> bool:
    t = getattr(s, "type", None)
    return t == "null" or str(t) == "DataType.NULL"


def _build_nullable_wrapper_overrides(components: Components) -> dict[str, TypeConversion]:
    """
    Collapse component schemas shaped like:
      X = anyOf/oneOf: [ $ref: Y, {type: null} ]
    into an override so refs to X become Optional[Y] without generating X.py.
    """
    overrides: dict[str, TypeConversion] = {}
    schemas = getattr(components, "schemas", None)
    if not schemas:
        return overrides

    for schema_name, schema in schemas.items():
        # Only non-discriminator wrappers
        if getattr(schema, "discriminator", None) is not None:
            continue

        variants = getattr(schema, "anyOf", None) or getattr(schema, "oneOf", None)
        if not variants or len(variants) != 2:
            continue

        ref = next((v for v in variants if isinstance(v, (Reference30, Reference31))), None)
        nul = next((v for v in variants if isinstance(v, (Schema30, Schema31)) and _is_null_schema(v)), None)
        if ref is None or nul is None:
            continue

        wrapper_name = common.normalize_symbol(schema_name)
        target_model = common.normalize_symbol(ref.ref.split("/")[-1])

        overrides[wrapper_name] = TypeConversion(
            original_type=ref.ref,
            converted_type=f"Optional[{target_model}]",
            import_types=[f"from .{target_model} import {target_model}"],
        )

    return overrides


def _get_discriminator_key(schema: Schema) -> Optional[str]:
    """
    Return discriminator property name if present on the schema.
    openapi-pydantic v3.x uses `schema.discriminator.propertyName` (common),
    but we defensively check a couple of variants.
    """
    disc = getattr(schema, "discriminator", None)
    if disc is None:
        return None

    # Most common: propertyName
    key = getattr(disc, "propertyName", None)
    if key:
        return str(key)

    # Defensive fallbacks
    key = getattr(disc, "property_name", None)
    if key:
        return str(key)

    return None


def _schema_is_union(schema: Schema) -> bool:
    used = schema.oneOf if schema.oneOf is not None else schema.anyOf
    return used is not None and len(used) > 0


def _alias_name_for_property(prop_name: str) -> str:
    # token_issuer -> TokenIssuer, foo-bar -> FooBar, etc.
    parts = re.split(r"[^a-zA-Z0-9]+", prop_name.strip())
    parts = [p for p in parts if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _dedupe_imports(imports: Optional[List[str]]) -> List[str]:
    if not imports:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for imp in imports:
        if imp and imp not in seen:
            seen.add(imp)
            out.append(imp)
    return out


def _render_union_alias_module(
    *,
    jinja_env,
    alias_name: str,
    union_type: str,
    discriminator_key: Optional[str],
    member_imports: List[str],
) -> str:
    return jinja_env.get_template(ALIAS_UNION_TEMPLATE).render(
        alias_name=alias_name,
        union_type=union_type,
        discriminator_key=discriminator_key,
        member_imports=_dedupe_imports(member_imports),
    )


@dataclass(frozen=True)
class DiscriminatorBinding:
    enum_name: str
    enum_member: str
    discriminator_key: str


def _enum_member_name(value: str) -> str:
    # Make a safe enum member name, e.g. "pkce" -> "PKCE", "oauth2" -> "OAUTH2"
    return common.normalize_symbol(str(value)).upper()


def _pascal_discriminator(discriminator_key: str) -> str:
    """
    Convert a discriminator property name (e.g. "type", "kind", "event_type")
    into a PascalCase suffix ("Type", "Kind", "EventType").
    """
    sym = common.normalize_symbol(discriminator_key)
    parts = [p for p in sym.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Discriminator"


def _pascal_schema_name(schema_name: str) -> str:
    """
    Convert a schema name like "token_issuer" into "TokenIssuer" for generated type/enum names.
    (We don't want enum class names like `token_issuerType`.)
    """
    sym = common.normalize_symbol(schema_name)
    parts = [p for p in sym.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Schema"


def _invert_discriminator_mapping(mapping: Optional[dict]) -> Dict[str, str]:
    """
    discriminator.mapping is usually { "<disc_value>": "<$ref>" }
    Return { "<$ref>": "<disc_value>" }
    """
    if not mapping:
        return {}
    inv: Dict[str, str] = {}
    for k, v in mapping.items():
        if isinstance(v, str):
            inv[v] = str(k)
    return inv


def _discover_discriminated_unions(
    components: Components,
) -> Tuple[Dict[str, DiscriminatorBinding], Dict[str, List[Tuple[str, str]]]]:
    """
    Discover discriminated unions in BOTH:
      - top-level component schemas (schema.discriminator + schema.oneOf)
      - inline/property schemas (property_schema.discriminator + property_schema.oneOf)

    Returns:
      - bindings: { "<MemberModelName>": DiscriminatorBinding(...) }
      - enum_members_by_name: { "<EnumName>": [(MEMBER_NAME, member_value), ...] }
    """
    bindings: Dict[str, DiscriminatorBinding] = {}
    enum_members_by_name: Dict[str, List[Tuple[str, str]]] = {}

    if not getattr(components, "schemas", None):
        return bindings, enum_members_by_name

    def register_union(alias_name: str, union_schema: Schema) -> None:
        disc = getattr(union_schema, "discriminator", None)
        one_of = getattr(union_schema, "oneOf", None)
        if disc is None or not one_of:
            return

        # openapi_pydantic uses propertyName, but be defensive
        discriminator_key = getattr(disc, "propertyName", None) or getattr(disc, "property_name", None)
        if not discriminator_key:
            return

        enum_name = f"{_pascal_schema_name(alias_name)}{_pascal_discriminator(discriminator_key)}"
        ref_to_value = _invert_discriminator_mapping(getattr(disc, "mapping", None))

        members: List[Tuple[str, str]] = []
        for sub in one_of:
            if not isinstance(sub, (Reference30, Reference31)):
                continue
            ref = sub.ref
            member_model = common.normalize_symbol(ref.split("/")[-1])
            disc_value = ref_to_value.get(ref) or member_model

            member_name = _enum_member_name(disc_value)
            members.append((member_name, disc_value))

            bindings[member_model] = DiscriminatorBinding(
                enum_name=enum_name,
                enum_member=member_name,
                discriminator_key=discriminator_key,
            )

        if members:
            # de-dupe by enum member name
            seen = set()
            deduped: List[Tuple[str, str]] = []
            for mn, mv in members:
                if mn in seen:
                    continue
                seen.add(mn)
                deduped.append((mn, mv))
            enum_members_by_name[enum_name] = deduped

    # 1) top-level discriminated unions
    for schema_name, schema in components.schemas.items():
        disc = getattr(schema, "discriminator", None)
        one_of = getattr(schema, "oneOf", None)
        if disc is not None and one_of:
            register_union(schema_name, schema)

    # 2) inline/property discriminated unions
    for _parent_name, parent_schema in components.schemas.items():
        props = getattr(parent_schema, "properties", None) or {}
        for prop_name, prop_schema in props.items():
            disc = getattr(prop_schema, "discriminator", None)
            one_of = getattr(prop_schema, "oneOf", None)
            if disc is not None and one_of:
                # alias name should be based on the property name (e.g. oauth2_client -> OAuth2Client)
                register_union(prop_name, prop_schema)

    return bindings, enum_members_by_name


def _build_discriminator_bindings(components: Components) -> Dict[str, DiscriminatorBinding]:
    """
    Scan components.schemas for discriminator-based oneOf schemas and return:
      { "<MemberModelName>": DiscriminatorBinding(...) }

    We use:
      - discriminator.propertyName as the key
      - discriminator.mapping (preferred) to get per-member discriminator values
      - fallback: schema name when mapping not present
    """
    bindings: Dict[str, DiscriminatorBinding] = {}

    if not getattr(components, "schemas", None):
        return bindings

    for schema_name, schema in components.schemas.items():
        disc = getattr(schema, "discriminator", None)
        one_of = getattr(schema, "oneOf", None)

        if disc is None or not one_of:
            continue

        discriminator_key = getattr(disc, "propertyName", None) or getattr(disc, "property_name", None)
        if discriminator_key is None:
            continue

        enum_name = f"{_pascal_schema_name(schema_name)}{_pascal_discriminator(discriminator_key)}"

        mapping = getattr(disc, "mapping", None) or {}
        # invert mapping to get $ref -> value
        ref_to_value: Dict[str, str] = {ref: value for value, ref in mapping.items()}

        for sub in one_of:
            if not (isinstance(sub, Reference30) or isinstance(sub, Reference31)):
                continue

            ref = sub.ref
            member_model = common.normalize_symbol(ref.split("/")[-1])

            disc_value = ref_to_value.get(ref)
            if disc_value is None:
                disc_value = member_model

            bindings[member_model] = DiscriminatorBinding(
                enum_name=enum_name,
                enum_member=_enum_member_name(disc_value),
                discriminator_key=discriminator_key,
            )

    return bindings


def _common_suffix(a: str, b: str) -> str:
    # longest common suffix
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


def _alias_name_for_response_union(member_models: List[str], fallback: str) -> str:
    suf = _common_suffix_many(member_models)
    suf = common.normalize_symbol(suf)
    if len(suf) >= 4:
        return suf
    return common.normalize_symbol(fallback)


def generate_response_union_alias_models(
    paths: Dict[str, PathItem],
    pydantic_version: PydanticVersion = PydanticVersion.V2,
) -> List[Model]:
    """
    Finds response schemas like:
      content.application/json.schema.oneOf + discriminator
    and emits a named alias module via alias_union.jinja2.
    """
    jinja_env = create_jinja_env()
    out: Dict[str, Model] = {}

    for _path_name, path in paths.items():
        for http_method in ["get", "post", "put", "delete", "patch", "head", "options", "trace"]:
            op: Optional[Operation] = getattr(path, http_method, None)
            if op is None or op.responses is None:
                continue

            for status_code, resp in op.responses.items():
                if not str(status_code).startswith("2"):
                    continue
                if not isinstance(resp, (Response30, Response31)):
                    continue

                content = getattr(resp, "content", None)
                if not isinstance(content, dict):
                    continue

                mt = content.get("application/json")
                if mt is None:
                    continue

                schema = getattr(mt, "media_type_schema", None)
                if not isinstance(schema, (Schema30, Schema31)):
                    continue

                disc_key = _get_discriminator_key(schema)
                used = schema.oneOf if schema.oneOf is not None else schema.anyOf
                if not disc_key or not used:
                    continue

                # Only support ref-only unions (your case)
                member_models: List[str] = []
                for sub in used:
                    if not isinstance(sub, (Reference30, Reference31)):
                        member_models = []
                        break
                    member_models.append(common.normalize_symbol(sub.ref.split("/")[-1]))
                if len(member_models) < 2:
                    continue

                alias_name = _alias_name_for_response_union(
                    member_models,
                    fallback=f"{common.normalize_symbol(op.operationId or 'Response')}Response",
                )

                # de-dupe
                if alias_name in out:
                    continue

                union_type = "Union[" + ", ".join(member_models) + "]"
                member_imports = [f"from .{m} import {m}" for m in member_models]

                alias_content = _render_union_alias_module(
                    jinja_env=jinja_env,
                    alias_name=alias_name,
                    union_type=union_type,
                    discriminator_key=disc_key,
                    member_imports=member_imports,
                )

                # placeholder schema for Model.openapi_object requirement
                placeholder_schema = Schema31() if isinstance(schema, Schema31) else Schema30()

                out[alias_name] = Model(
                    file_name=alias_name,
                    content=alias_content,
                    openapi_object=placeholder_schema,
                    properties=[],
                )

    return list(out.values())


def type_converter(  # noqa: C901
    schema: Union[Schema, Reference],
    required: bool = False,
    model_name: Optional[str] = None,
) -> TypeConversion:
    """
    Converts an OpenAPI type to a Python type.
    :param schema: Schema or Reference containing the type to be converted
    :param model_name: Name of the original model on which the type is defined
    :param required: Flag indicating if the type is required by the class
    :return: The converted type
    """
    # Handle Reference objects by converting them to type references
    if isinstance(schema, Reference30) or isinstance(schema, Reference31):
        import_type = common.normalize_symbol(schema.ref.split("/")[-1])
        # Nullable-wrapper collapse: ref to X may be overridden to Optional[Y]
        override = _REFERENCE_TYPE_OVERRIDES.get(import_type)
        if override is not None:
            return TypeConversion(
                original_type=schema.ref,
                converted_type=override.converted_type,
                import_types=override.import_types,
            )

        if required:
            converted_type = import_type
        else:
            converted_type = f"Optional[{import_type}]"

        return TypeConversion(
            original_type=schema.ref,
            converted_type=converted_type,
            import_types=([f"from .{import_type} import {import_type}"] if import_type != model_name else None),
        )

    if required:
        pre_type = ""
        post_type = ""
    else:
        pre_type = "Optional["
        post_type = "]"

    original_type = (
        schema.type.value
        if hasattr(schema.type, "value") and schema.type is not None
        else str(schema.type)
        if schema.type is not None
        else "object"
    )
    import_types: Optional[List[str]] = None

    if schema.allOf is not None:
        raise NotImplementedError("Inline allOf types are unsupported; define a named object component for composition")

    elif schema.oneOf is not None or schema.anyOf is not None:
        used = schema.oneOf if schema.oneOf is not None else schema.anyOf
        used = used if used is not None else []
        # Special-case inline nullable wrapper: (ref | null) => Optional[Ref]
        if len(used) == 2:
            ref = next((v for v in used if isinstance(v, (Reference30, Reference31))), None)
            nul = next((v for v in used if isinstance(v, (Schema30, Schema31)) and _is_null_schema(v)), None)
            if ref is not None and nul is not None:
                import_type = common.normalize_symbol(ref.ref.split("/")[-1])
                override = _REFERENCE_TYPE_OVERRIDES.get(import_type)
                if override is not None:
                    return TypeConversion(
                        original_type=f"union<{ref.ref},null>",
                        converted_type=override.converted_type,
                        import_types=override.import_types,
                    )
                return TypeConversion(
                    original_type=f"union<{ref.ref},null>",
                    converted_type=f"Optional[{import_type}]",
                    import_types=([f"from .{import_type} import {import_type}"] if import_type != model_name else None),
                )
        conversions = []
        for sub_schema in used:
            if isinstance(sub_schema, Schema30) or isinstance(sub_schema, Schema31):
                conversions.append(type_converter(sub_schema, True))
            else:
                import_type = common.normalize_symbol(sub_schema.ref.split("/")[-1])
                import_types = [f"from .{import_type} import {import_type}"]
                conversions.append(
                    TypeConversion(
                        original_type=sub_schema.ref,
                        converted_type=import_type,
                        import_types=import_types,
                    )
                )
        original_type = "union<" + ",".join([i.original_type for i in conversions]) + ">"

        if len(conversions) == 1:
            converted_type = conversions[0].converted_type
        else:
            converted_type = "Union[" + ",".join([i.converted_type for i in conversions]) + "]"

        converted_type = pre_type + converted_type + post_type
        import_types = list(itertools.chain(*[i.import_types for i in conversions if i.import_types is not None]))
    # We only want to auto convert to datetime if orjson is used throughout the code, otherwise we can not
    # serialize it to JSON.
    elif (schema.type == "string" or str(schema.type) == "DataType.STRING") and (
        schema.schema_format is None or not common.get_use_orjson()
    ):
        converted_type = pre_type + "str" + post_type
    elif (
        (schema.type == "string" or str(schema.type) == "DataType.STRING")
        and schema.schema_format is not None
        and schema.schema_format.startswith("uuid")
        and common.get_use_orjson()
    ):
        if len(schema.schema_format) > 4 and schema.schema_format[4].isnumeric():
            uuid_type = schema.schema_format.upper()
            converted_type = pre_type + uuid_type + post_type
            import_types = ["from pydantic import " + uuid_type]
        else:
            converted_type = pre_type + "UUID" + post_type
            import_types = ["from uuid import UUID"]
    elif (schema.type == "string" or str(schema.type) == "DataType.STRING") and schema.schema_format == "date-time":
        converted_type = pre_type + "datetime" + post_type
        import_types = ["from datetime import datetime"]
    elif schema.type == "integer" or str(schema.type) == "DataType.INTEGER":
        converted_type = pre_type + "int" + post_type
    elif schema.type == "number" or str(schema.type) == "DataType.NUMBER":
        converted_type = pre_type + "float" + post_type
    elif schema.type == "boolean" or str(schema.type) == "DataType.BOOLEAN":
        converted_type = pre_type + "bool" + post_type
    elif schema.type == "array" or str(schema.type) == "DataType.ARRAY":
        retVal = pre_type + "List["
        item_imports: List[str] = []
        if isinstance(schema.items, Reference30) or isinstance(schema.items, Reference31):
            converted_reference = _generate_property_from_reference(
                model_name or "", "", schema.items, schema, required
            )
            import_types = converted_reference.type.import_types
            original_type = "array<" + converted_reference.type.original_type + ">"
            retVal += converted_reference.type.converted_type
        elif isinstance(schema.items, Schema30) or isinstance(schema.items, Schema31):
            type_str = schema.items.type
            if hasattr(type_str, "value"):
                type_value = str(type_str.value) if type_str is not None else "unknown"
            else:
                type_value = str(type_str) if type_str is not None else "unknown"
            original_type = "array<" + type_value + ">"
            # IMPORTANT: propagate imports from the nested schema (e.g. Union[$ref...])
            item_conv = type_converter(schema.items, True, model_name=model_name)
            retVal += item_conv.converted_type
            if item_conv.import_types:
                item_imports.extend(item_conv.import_types)
        else:
            original_type = "array<unknown>"
            retVal += "Any"

        converted_type = retVal + "]" + post_type

        # Merge imports from the items schema (when items is a Schema, not a Reference)
        if item_imports:
            import_types = _dedupe_imports((import_types or []) + item_imports)
    elif schema.type == "object" or str(schema.type) == "DataType.OBJECT":
        # Support "map" objects: type=object + additionalProperties schema/ref
        addl = getattr(schema, "additionalProperties", None)
        if isinstance(addl, (Reference30, Reference31)):
            # Dict[str, <ref>]
            v = type_converter(addl, required=True, model_name=model_name)
            converted_type = f"{pre_type}Dict[str, {v.converted_type}]{post_type}"
            import_types = _dedupe_imports((import_types or []) + (v.import_types or [])) or None
        elif isinstance(addl, (Schema30, Schema31)):
            # Dict[str, <schema>], including Union[...] etc.
            v = type_converter(addl, required=True, model_name=model_name)
            converted_type = f"{pre_type}Dict[str, {v.converted_type}]{post_type}"
            import_types = _dedupe_imports((import_types or []) + (v.import_types or [])) or None
        else:
            converted_type = pre_type + "Dict[str, Any]" + post_type
    elif schema.type == "null" or str(schema.type) == "DataType.NULL":
        converted_type = pre_type + "None" + post_type
    elif schema.type is None:
        converted_type = pre_type + "Any" + post_type
    else:
        # Handle DataType enum types as strings
        if hasattr(schema.type, "value"):
            # Single DataType enum
            if schema.type.value == "string":
                # Check for UUID format first
                if (
                    schema.schema_format is not None
                    and schema.schema_format.startswith("uuid")
                    and common.get_use_orjson()
                ):
                    if len(schema.schema_format) > 4 and schema.schema_format[4].isnumeric():
                        uuid_type = schema.schema_format.upper()
                        converted_type = pre_type + uuid_type + post_type
                        import_types = ["from pydantic import " + uuid_type]
                    else:
                        converted_type = pre_type + "UUID" + post_type
                        import_types = ["from uuid import UUID"]
                # Check for date-time format
                elif schema.schema_format == "date-time":
                    converted_type = pre_type + "datetime" + post_type
                    import_types = ["from datetime import datetime"]
                else:
                    converted_type = pre_type + "str" + post_type
            elif schema.type.value == "integer":
                converted_type = pre_type + "int" + post_type
            elif schema.type.value == "number":
                converted_type = pre_type + "float" + post_type
            elif schema.type.value == "boolean":
                converted_type = pre_type + "bool" + post_type
            elif schema.type.value == "array":
                converted_type = pre_type + "List[Any]" + post_type
            elif schema.type.value == "object":
                converted_type = pre_type + "Dict[str, Any]" + post_type
            elif schema.type.value == "null":
                converted_type = pre_type + "None" + post_type
            else:
                converted_type = pre_type + "str" + post_type  # Default fallback
        elif isinstance(schema.type, list) and len(schema.type) > 0:
            # List of DataType enums - use first one
            first_type = schema.type[0]
            if hasattr(first_type, "value"):
                if first_type.value == "string":
                    # Check for UUID format first
                    if (
                        schema.schema_format is not None
                        and schema.schema_format.startswith("uuid")
                        and common.get_use_orjson()
                    ):
                        if len(schema.schema_format) > 4 and schema.schema_format[4].isnumeric():
                            uuid_type = schema.schema_format.upper()
                            converted_type = pre_type + uuid_type + post_type
                            import_types = ["from pydantic import " + uuid_type]
                        else:
                            converted_type = pre_type + "UUID" + post_type
                            import_types = ["from uuid import UUID"]
                    # Check for date-time format
                    elif schema.schema_format == "date-time":
                        converted_type = pre_type + "datetime" + post_type
                        import_types = ["from datetime import datetime"]
                    else:
                        converted_type = pre_type + "str" + post_type
                elif first_type.value == "integer":
                    converted_type = pre_type + "int" + post_type
                elif first_type.value == "number":
                    converted_type = pre_type + "float" + post_type
                elif first_type.value == "boolean":
                    converted_type = pre_type + "bool" + post_type
                elif first_type.value == "array":
                    converted_type = pre_type + "List[Any]" + post_type
                elif first_type.value == "object":
                    converted_type = pre_type + "Dict[str, Any]" + post_type
                elif first_type.value == "null":
                    converted_type = pre_type + "None" + post_type
                else:
                    converted_type = pre_type + "str" + post_type  # Default fallback
            else:
                converted_type = pre_type + "str" + post_type  # Default fallback
        else:
            converted_type = pre_type + "str" + post_type  # Default fallback

    return TypeConversion(
        original_type=original_type,
        converted_type=converted_type,
        import_types=import_types,
    )


def _generate_property_from_schema(
    model_name: str, name: str, schema: Schema, parent_schema: Optional[Schema] = None
) -> Property:
    """
    Generates a property from a schema. It takes the type of the schema and converts it to a python type, and then
    creates the according property.
    :param model_name: Name of the model this property belongs to
    :param name: Name of the schema
    :param schema: schema to be converted
    :param parent_schema: Component this belongs to
    :return: Property
    """
    required = parent_schema is not None and parent_schema.required is not None and name in parent_schema.required

    # Pick up OpenAPI default (if present)
    default_val = getattr(schema, "default", None)

    if required:
        rendered_default = None
    else:
        # If OpenAPI provides a default, use it. Otherwise fall back to None.
        rendered_default = repr(default_val) if default_val is not None else "None"

    return Property(
        name=name,
        type=type_converter(schema, required, model_name),
        required=required,
        default=rendered_default,
        import_type=None if not required else ([] if name == model_name else [name]),
    )


def _generate_property_from_reference(
    model_name: str,
    name: str,
    reference: Reference,
    parent_schema: Optional[Schema] = None,
    force_required: bool = False,
) -> Property:
    """
    Generates a property from a reference. It takes the name of the reference as the type, and then
    returns a property type
    :param name: Name of the schema
    :param reference: reference to be converted
    :param parent_schema: Component this belongs to
    :param force_required: Force the property to be required
    :return: Property and model to be imported by the file
    """
    required = (
        parent_schema is not None and parent_schema.required is not None and name in parent_schema.required
    ) or force_required
    import_model = common.normalize_symbol(reference.ref.split("/")[-1])

    if import_model == model_name:
        type_conv = TypeConversion(
            original_type=reference.ref,
            converted_type=(import_model if required else 'Optional["' + import_model + '"]'),
            import_types=None,
        )
    else:
        type_conv = TypeConversion(
            original_type=reference.ref,
            converted_type=(import_model if required else "Optional[" + import_model + "]"),
            import_types=[f"from .{import_model} import {import_model}"],
        )
    return Property(
        name=name,
        type=type_conv,
        required=required,
        default=None if required else "None",
        import_type=[import_model],
    )


def generate_models(components: Components, pydantic_version: PydanticVersion = PydanticVersion.V2) -> List[Model]:
    """
    Receives components from an OpenAPI 3.0+ specification and generates the models from it.
    Additionally:
      - Detects unions / discriminated unions in property schemas (oneOf/anyOf)
      - Emits a named alias module (e.g. TokenIssuer.py)
      - Rewrites the property type to use that alias (instead of Union[...])
    """
    models: List[Model] = []

    if components.schemas is None:
        return models

    jinja_env = create_jinja_env()
    # Build nullable-wrapper overrides so refs to simple wrappers (X = anyOf[$ref Y, null])
    # are collapsed to Optional[Y] and we avoid generating X.py (which can collide on Windows).
    global _REFERENCE_TYPE_OVERRIDES
    _REFERENCE_TYPE_OVERRIDES = _build_nullable_wrapper_overrides(components)

    discriminator_bindings, enum_members_by_name = _discover_discriminated_unions(components)

    # Track alias modules so we only create each once
    alias_models_by_name: Dict[str, Model] = {}

    for schema_name, schema_or_reference in components.schemas.items():
        name = common.normalize_symbol(schema_name)

        # Don't generate standalone modules for nullable wrapper components
        if name in _REFERENCE_TYPE_OVERRIDES:
            continue

        # --------------------------
        # Enums
        # --------------------------
        if schema_or_reference.enum is not None:
            value_dict = schema_or_reference.model_dump()
            value_dict["enum"] = [(common.normalize_symbol(str(i)).upper(), i) for i in value_dict["enum"]]
            m = Model(
                file_name=name,
                content=jinja_env.get_template(ENUM_TEMPLATE).render(name=name, **value_dict),
                openapi_object=schema_or_reference,
                properties=[],
            )
            try:
                compile(m.content, "<string>", "exec")
                models.append(m)
            except SyntaxError as e:  # pragma: no cover
                click.echo(f"Error in model {name}: {e}")

            continue  # pragma: no cover

        # --------------------------
        # Normal models
        # --------------------------
        properties: List[Property] = []
        resolved_schema = _resolve_allof_schema(schema_or_reference, components.schemas, stack=(schema_name,))
        effective_schema = schema_or_reference.model_copy(
            update={"properties": resolved_schema.properties, "required": sorted(resolved_schema.required)}
        )
        property_iterator = resolved_schema.properties.items()

        for prop_name, prop_schema in property_iterator:
            # Reference property
            if isinstance(prop_schema, Reference30) or isinstance(prop_schema, Reference31):
                conv_property = _generate_property_from_reference(name, prop_name, prop_schema, effective_schema)
                properties.append(conv_property)
                continue

            # Schema property
            conv_property = _generate_property_from_schema(name, prop_name, prop_schema, effective_schema)

            # --------------------------
            # const / single-value enum constrains values, not field presence.
            # --------------------------
            if isinstance(prop_schema, (Schema30, Schema31)):
                const_val = getattr(prop_schema, "const", None)
                enum_vals = getattr(prop_schema, "enum", None)

                literal_val = None
                if const_val is not None:
                    literal_val = const_val
                elif isinstance(enum_vals, list) and len(enum_vals) == 1:
                    literal_val = enum_vals[0]

                if literal_val is not None:
                    literal_type = f"Literal[{literal_val!r}]"
                    conv_property.type = TypeConversion(
                        original_type=conv_property.type.original_type,
                        converted_type=literal_type if conv_property.required else f"Optional[{literal_type}]",
                        import_types=_dedupe_imports(
                            (conv_property.type.import_types or []) + ["from typing import Literal"]
                        ),
                    )

            if prop_name in resolved_schema.intersected_properties:
                _constrain_allof_property(conv_property, prop_schema)

            # If this model is a discriminated union member, and this property
            # is the discriminator key, make it a Literal[...] with a default
            binding = discriminator_bindings.get(name)
            if binding and common.normalize_symbol(conv_property.name) == common.normalize_symbol(
                binding.discriminator_key
            ):
                conv_property.required = True
                conv_property.default = f"{binding.enum_name}.{binding.enum_member}"

                extra_imports = [
                    "from typing import Literal",
                    f"from .{binding.enum_name} import {binding.enum_name}",
                ]

                conv_property.type = TypeConversion(
                    original_type=conv_property.type.original_type,
                    converted_type=f"Literal[{binding.enum_name}.{binding.enum_member}]",
                    import_types=extra_imports,
                )

            # -----------------------------------------
            # NEW: union / discriminated union factoring
            # -----------------------------------------
            if isinstance(prop_schema, (Schema30, Schema31)) and _schema_is_union(prop_schema):
                alias_name = _alias_name_for_property(prop_name)
                discriminator_key = _get_discriminator_key(prop_schema)

                # Only generate standalone alias modules for DISCRIMINATED unions.
                # Plain unions (including nullable wrappers) are left inline.
                if discriminator_key is not None:
                    # Build the union type and gather imports from members.
                    # Important: we want a NON-optional union for the alias definition.
                    union_conv = type_converter(prop_schema, required=True, model_name=name)
                    union_type_str = union_conv.converted_type  # e.g. Union[A,B,C]
                    member_imports = union_conv.import_types or []

                    # Create alias module once
                    if alias_name not in alias_models_by_name:
                        alias_content = _render_union_alias_module(
                            jinja_env=jinja_env,
                            alias_name=alias_name,
                            union_type=union_type_str,
                            discriminator_key=discriminator_key,
                            member_imports=member_imports,
                        )

                        # Validate alias module compiles
                        try:
                            compile(alias_content, "<string>", "exec")
                        except SyntaxError as e:  # pragma: no cover
                            click.echo(f"Error in union alias {alias_name}: {e}")  # pragma: no cover

                        alias_models_by_name[alias_name] = Model(
                            file_name=alias_name,
                            content=alias_content,
                            openapi_object=prop_schema,
                            properties=[],
                        )

                    # Rewrite property type to use alias
                    rewritten_type = alias_name if conv_property.required else f"Optional[{alias_name}]"
                    conv_property.type = TypeConversion(
                        original_type=conv_property.type.original_type,
                        converted_type=rewritten_type,
                        import_types=[f"from .{alias_name} import {alias_name}"],
                    )

            properties.append(conv_property)

        template_name = MODELS_TEMPLATE_PYDANTIC_V2 if pydantic_version == PydanticVersion.V2 else MODELS_TEMPLATE

        generated_content = jinja_env.get_template(template_name).render(
            schema_name=name, schema=schema_or_reference, properties=properties
        )

        try:
            compile(generated_content, "<string>", "exec")
        except SyntaxError as e:  # pragma: no cover
            click.echo(f"Error in model {name}: {e}")  # pragma: no cover

        models.append(
            Model(
                file_name=name,
                content=generated_content,
                openapi_object=schema_or_reference,
                properties=properties,
            )
        )

    # Ensure enum modules for discriminators are included
    enum_models: List[Model] = []
    for enum_name, members in enum_members_by_name.items():
        enum_content = jinja_env.get_template(DISCRIMINATOR_ENUM_TEMPLATE).render(enum_name=enum_name, members=members)
        try:
            compile(enum_content, "<string>", "exec")
        except SyntaxError as e:  # pragma: no cover
            click.echo(f"Error in enum {enum_name}: {e}")  # pragma: no cover

        # Model.openapi_object is required (non-Optional). Enum modules don't map to a real schema,
        # so attach a tiny placeholder schema to satisfy validation.
        placeholder_schema = Schema31() if isinstance(components, Components31) else Schema30()

        enum_models.append(
            Model(file_name=enum_name, content=enum_content, openapi_object=placeholder_schema, properties=[])
        )

    # Ensure alias modules are included in output
    models.extend(alias_models_by_name.values())
    # Append enum modules last
    models.extend(enum_models)

    return models
