import pytest
from scripts.check_release_version import validate_release_tag


def test_release_tag_must_match_project_version() -> None:
    validate_release_tag("v0.1.0", "0.1.0")


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1.1", "v0.1.0rc1", "vlatest"])
def test_release_tag_rejects_versions_other_than_project_version(tag: str) -> None:
    with pytest.raises(ValueError, match="must match project version"):
        validate_release_tag(tag, "0.1.0")
