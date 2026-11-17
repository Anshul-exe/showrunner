"""Every customer YAML is valid against the schema and matches its path."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS = ROOT / "platform" / "customers"
SCHEMA = json.loads((CUSTOMERS / "schema.json").read_text())
SPECS = sorted(CUSTOMERS.glob("*/*.yaml"))


def test_there_are_customers():
    assert len(SPECS) >= 2


@pytest.mark.parametrize("path", SPECS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_spec_is_valid(path):
    spec = yaml.safe_load(path.read_text())
    jsonschema.validate(spec, SCHEMA)
    assert spec["customer"] == path.parent.name
    assert spec["env"] == path.stem


def test_schema_rejects_unknown_feature():
    bad = {"customer": "x-tv", "env": "dev", "owner": "t", "cost_center": "c", "features": {"eks": True}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
