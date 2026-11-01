"""Structural TMSL union without rewriting DAX/M or silently choosing conflicts."""

import base64
import copy
import json

from .util import MergeError, digest, require


_MISSING = object()


def additive_collection(path, value):
    """Only known object collections may be added to an existing common object."""
    parts = path.split("/")[1:]
    allowed = (
        len(parts) == 3
        and parts[:2] == ["bim", "model"]
        and parts[-1]
        in {
            "tables",
            "relationships",
            "roles",
            "cultures",
            "perspectives",
            "dataSources",
            "expressions",
        }
    ) or (
        len(parts) == 5
        and parts[:3] == ["bim", "model", "tables"]
        and parts[-1] in {"columns", "measures", "partitions", "hierarchies"}
    )
    return (
        allowed
        and isinstance(value, list)
        and all(
            isinstance(item, dict) and isinstance(item.get("name"), str)
            for item in value
        )
    )


def pointer(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def decode_definition(response):
    definition = response.get("definition", response)
    require(
        isinstance(definition.get("parts"), list),
        "No semantic-model definition parts returned",
    )
    parts = {}
    for part in definition["parts"]:
        path = part["path"]
        require(path not in parts, "Duplicate definition part: " + path)
        require(
            part.get("payloadType") == "InlineBase64",
            "Unsupported definition payload type",
        )
        try:
            parts[path] = base64.b64decode(part["payload"], validate=True).decode(
                "utf-8-sig"
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise MergeError("Invalid base64/UTF-8 definition part: " + path) from exc
    require(
        "model.bim" in parts and "definition.pbism" in parts,
        "Request getDefinition?format=TMSL; model.bim and definition.pbism are required",
    )
    try:
        bim = json.loads(parts["model.bim"])
        pbism = json.loads(parts["definition.pbism"])
    except json.JSONDecodeError as exc:
        raise MergeError("Invalid TMSL/definition.pbism JSON") from exc
    require(
        isinstance(bim.get("model"), dict), "model.bim does not contain a tabular model"
    )
    return {
        "bim": bim,
        "pbism": pbism,
        "extras": {
            p: v
            for p, v in parts.items()
            if p
            not in ("model.bim", "definition.pbism", ".platform", "diagramLayout.json")
        },
    }


def encode_definition(model):
    texts = {
        "model.bim": json.dumps(model["bim"], ensure_ascii=False),
        "definition.pbism": json.dumps(model["pbism"], ensure_ascii=False),
        **model.get("extras", {}),
    }
    return {
        "format": "TMSL",
        "parts": [
            {
                "path": path,
                "payloadType": "InlineBase64",
                "payload": base64.b64encode(text.encode()).decode(),
            }
            for path, text in sorted(texts.items())
        ],
    }


class Merger:
    def __init__(self, resolutions=None):
        self.resolutions = resolutions or {}
        self.conflicts = []
        self.decisions = []
        self.used = set()

    @staticmethod
    def _copy(value):
        return _MISSING if value is _MISSING else copy.deepcopy(value)

    def _conflict(self, left, right, path, reason):
        self.conflicts.append(
            {
                "path": path,
                "left": None if left is _MISSING else copy.deepcopy(left),
                "right": None if right is _MISSING else copy.deepcopy(right),
                "left_present": left is not _MISSING,
                "right_present": right is not _MISSING,
                "reason": reason,
            }
        )
        return self._copy(left)  # Preview only; conflicts block deployment.

    def merge(self, left, right, path=""):
        if left == right:
            return self._copy(left)
        if path in self.resolutions:
            value = self.resolutions[path]
            self.used.add(path)
            require(isinstance(value, dict), "Resolution must be an object: " + path)
            if "value" in value:
                result = value["value"]
            else:
                require(
                    value.get("take") in ("left", "right"),
                    "Resolution must select left/right or provide value: " + path,
                )
                result = left if value["take"] == "left" else right
            self.decisions.append(
                {
                    "path": path,
                    "resolution": value,
                    "left_sha256": None if left is _MISSING else digest(left),
                    "right_sha256": None if right is _MISSING else digest(right),
                    "left_present": left is not _MISSING,
                    "right_present": right is not _MISSING,
                }
            )
            return self._copy(result)
        if path.rsplit("/", 1)[-1] in ("lineageTag", "sourceLineageTag"):
            if left is _MISSING or right is _MISSING:
                return self._copy(right if left is _MISSING else left)
            self.decisions.append(
                {
                    "path": path,
                    "resolution": "retain left lineage metadata",
                    "right_value": right,
                }
            )
            return copy.deepcopy(left)
        if left is _MISSING or right is _MISSING:
            present = right if left is _MISSING else left
            if additive_collection(path, present):
                return copy.deepcopy(present)
            return self._conflict(
                left,
                right,
                path,
                "Property omitted in one source; its default is not assumed equivalent. Resolve explicitly, including which source's omission to preserve.",
            )
        if isinstance(left, dict) and isinstance(right, dict):
            result = {}
            for key in dict.fromkeys([*left, *right]):
                value = self.merge(
                    left.get(key, _MISSING),
                    right.get(key, _MISSING),
                    pointer(path, key),
                )
                if value is not _MISSING:
                    result[key] = value
            return result
        if isinstance(left, list) and isinstance(right, list):
            if not left:
                return copy.deepcopy(right)
            if not right:
                return copy.deepcopy(left)
            if all(
                isinstance(v, dict) and isinstance(v.get("name"), str)
                for v in left + right
            ):
                result = copy.deepcopy(left)
                by_name = {}
                for index, value in enumerate(left):
                    key = value["name"].casefold()
                    require(
                        key not in by_name, "Duplicate case-insensitive name at " + path
                    )
                    by_name[key] = index
                right_names = set()
                for value in right:
                    key = value["name"].casefold()
                    require(
                        key not in right_names,
                        "Duplicate case-insensitive name in right model at " + path,
                    )
                    right_names.add(key)
                    if key in by_name:
                        index = by_name[key]
                        normalized = copy.deepcopy(value)
                        normalized["name"] = result[index]["name"]
                        result[index] = self.merge(
                            result[index],
                            normalized,
                            pointer(path, result[index]["name"]),
                        )
                    else:
                        by_name[key] = len(result)
                        result.append(copy.deepcopy(value))
                return result
        return self._conflict(
            left,
            right,
            path,
            "Different definitions require an explicit resolution; neither source is preferred",
        )


def merge_models(left, right, display_name, resolutions=None):
    engine = Merger(resolutions)
    lhs, rhs = copy.deepcopy(left), copy.deepcopy(right)
    # Database identity belongs to the new item, not either source.
    for value in (lhs["bim"], rhs["bim"]):
        value.pop("name", None)
        value.pop("id", None)
    result = engine.merge(lhs, rhs)
    result["bim"]["name"] = display_name
    require(
        not (set(engine.resolutions) - engine.used),
        "Unused/misspelled resolution paths: "
        + str(sorted(set(engine.resolutions) - engine.used)),
    )
    issues = validate_model(result["bim"])
    return {
        "model": result,
        "definition": encode_definition(result),
        "conflicts": engine.conflicts,
        "decisions": engine.decisions,
        "issues": issues,
        "summary": summary(result["bim"]),
        "ready": not engine.conflicts and not issues,
    }


def summary(bim):
    model = bim["model"]
    tables = model.get("tables", [])
    return {
        "tables": len(tables),
        "columns": sum(len(t.get("columns", [])) for t in tables),
        "measures": sum(len(t.get("measures", [])) for t in tables),
        "relationships": len(model.get("relationships", [])),
        "roles": len(model.get("roles", [])),
        "partitions": sum(len(t.get("partitions", [])) for t in tables),
        "modes": sorted(
            {
                p.get("mode", model.get("defaultMode", "import"))
                for t in tables
                for p in t.get("partitions", [])
            }
        ),
    }


def validate_model(bim):
    issues, measures = [], {}
    model = bim.get("model", {})
    tables = {}
    for table in model.get("tables", []):
        key = table["name"].casefold()
        if key in tables:
            issues.append("Duplicate table: " + table["name"])
        tables[key] = {c["name"].casefold() for c in table.get("columns", [])}
        for measure in table.get("measures", []):
            name = measure["name"].casefold()
            if name in measures:
                issues.append(
                    f"Measure name {measure['name']} occurs in both {measures[name]} and {table['name']}; reports cannot be silently renamed"
                )
            measures[name] = table["name"]
        for collection in ("columns", "measures", "partitions"):
            seen = set()
            for item in table.get(collection, []):
                name = item["name"].casefold()
                if name in seen:
                    issues.append("Duplicate " + collection + " name: " + item["name"])
                seen.add(name)
        columns = {c["name"].casefold(): c for c in table.get("columns", [])}
        for measure in table.get("measures", []):
            if measure["name"].casefold() in columns:
                issues.append(
                    "Measure and column share a name in the same table: "
                    + table["name"]
                    + "."
                    + measure["name"]
                )
        calculated_table = any(
            p.get("source", {}).get("type") == "calculated"
            for p in table.get("partitions", [])
        )
        for column in columns.values():
            if calculated_table and column.get("type", "data") not in (
                "calculatedTableColumn",
                "calculated",
            ):
                issues.append(
                    "Calculated-table output needs type=calculatedTableColumn: "
                    + table["name"]
                    + "."
                    + column["name"]
                )
            if (
                column.get("sortByColumn")
                and column["sortByColumn"].casefold() not in columns
            ):
                issues.append(
                    "Missing sortByColumn for " + table["name"] + "." + column["name"]
                )
    for relation in model.get("relationships", []):
        for side in ("from", "to"):
            table = relation.get(side + "Table", "").casefold()
            column = relation.get(side + "Column", "").casefold()
            if table not in tables or column not in tables.get(table, set()):
                issues.append(
                    "Broken relationship endpoint: " + relation["name"] + "/" + side
                )
    return issues


def quote_table(value):
    return "'" + value.replace("'", "''") + "'"


def quote_measure(value):
    return "[" + value.replace("]", "]]") + "]"


def default_probes(model, source):
    probes = []
    for table in model["bim"]["model"].get("tables", []):
        name = table["name"]
        probes.append(
            {
                "name": source + ":rows:" + name,
                "source": source,
                "query": f'EVALUATE ROW("Value", COUNTROWS({quote_table(name)}))',
            }
        )
        for measure in table.get("measures", []):
            probes.append(
                {
                    "name": source + ":measure:" + name + ":" + measure["name"],
                    "source": source,
                    "query": f'EVALUATE ROW("Value", {quote_table(name)}{quote_measure(measure["name"])} )',
                }
            )
    return probes
