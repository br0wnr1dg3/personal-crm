import pytest
from netcrm.normalize import company_key

@pytest.mark.parametrize("raw,expected", [
    ("Acme Inc.", "acme"),
    ("ACME, Inc", "acme"),
    ("Acme Inc", "acme"),
    ("acme   inc.", "acme"),
    ("Initech Ltd.", "initech"),
    ("Globex GmbH", "globex"),
    ("Nuts & Bolts AI", "nuts and bolts ai"),
    ("Nuts and Bolts AI", "nuts and bolts ai"),
    ("Cogent Labs Ltd.", "cogent labs"),
    ("Massive Dynamic", "massive dynamic"),
    ("Stark Industries", "stark industries"),
    ("  Wayne Enterprises  ", "wayne enterprises"),
    ("Foo, LLC", "foo"),
    ("Foo, B.V.", "foo"),
    ("Foo, S.A.", "foo"),
    ("Foo S.r.l.", "foo"),
    ("Foo plc", "foo"),
    ("", ""),
    (None, ""),
])
def test_company_key(raw, expected):
    assert company_key(raw) == expected
