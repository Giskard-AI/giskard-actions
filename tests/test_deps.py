"""Tests for pure parsing functions in deps.py (no git calls)."""

from release_notes.deps import (
    _dependency_bumps_for_file,
    _parse_package_json_deps,
    _parse_pom_versions,
    _parse_pyproject_deps,
)

MINIMAL_PYPROJECT = """
[project]
name = "myapp"
dependencies = [
    "requests>=2.28.0",
    "click==8.1.3",
]
"""

MINIMAL_PACKAGE_JSON = """{
  "dependencies": {
    "react": "^18.0.0",
    "axios": "^1.0.0"
  },
  "devDependencies": {
    "jest": "^29.0.0",
    "react": "^17.0.0"
  }
}"""

MINIMAL_POM = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <properties>
    <spring.version>5.3.0</spring.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>5.3.0</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>${spring.version}</version>
    </dependency>
  </dependencies>
</project>"""


class TestParsePyprojectDeps:
    def test_basic_deps(self):
        result = _parse_pyproject_deps(MINIMAL_PYPROJECT)
        assert "requests" in result
        assert result["requests"] == ">=2.28.0"

    def test_pinned_dep(self):
        result = _parse_pyproject_deps(MINIMAL_PYPROJECT)
        assert result["click"] == "8.1.3"

    def test_empty_deps(self):
        result = _parse_pyproject_deps("[project]\nname = 'x'\n")
        assert result == {}


class TestParsePackageJsonDeps:
    def test_prod_deps(self):
        result = _parse_package_json_deps(MINIMAL_PACKAGE_JSON)
        assert result["react"] == "^18.0.0"
        assert result["axios"] == "^1.0.0"

    def test_dev_deps_suffixed_when_conflict(self):
        result = _parse_package_json_deps(MINIMAL_PACKAGE_JSON)
        # "react" exists in prod, so dev version should be "react (dev)"
        assert "react (dev)" in result
        assert result["react (dev)"] == "^17.0.0"

    def test_dev_deps_no_conflict(self):
        result = _parse_package_json_deps(MINIMAL_PACKAGE_JSON)
        assert result["jest"] == "^29.0.0"

    def test_empty_json(self):
        result = _parse_package_json_deps("{}")
        assert result == {}


class TestParsePomVersions:
    def test_properties(self):
        result = _parse_pom_versions(MINIMAL_POM)
        assert result["spring.version"] == "5.3.0"

    def test_dependency_with_literal_version(self):
        result = _parse_pom_versions(MINIMAL_POM)
        assert result["org.springframework:spring-core"] == "5.3.0"

    def test_excludes_property_placeholder(self):
        result = _parse_pom_versions(MINIMAL_POM)
        # junit uses ${spring.version} — should be excluded
        assert "junit:junit" not in result


class TestDependencyBumpsForFile:
    def test_pyproject_bump_detected(self):
        before = "[project]\nname='x'\ndependencies=['requests>=2.0.0']\n"
        after = "[project]\nname='x'\ndependencies=['requests>=2.28.0']\n"
        bumps = _dependency_bumps_for_file(
            before=before, after=after, manifest_path="pyproject.toml"
        )
        assert len(bumps) == 1
        assert bumps[0].name == "requests"
        assert bumps[0].from_version == ">=2.0.0"
        assert bumps[0].to_version == ">=2.28.0"

    def test_no_change_no_bump(self):
        toml = "[project]\nname='x'\ndependencies=['requests>=2.0.0']\n"
        bumps = _dependency_bumps_for_file(before=toml, after=toml, manifest_path="pyproject.toml")
        assert bumps == []

    def test_none_before_returns_empty(self):
        assert (
            _dependency_bumps_for_file(before=None, after="x", manifest_path="pyproject.toml") == []
        )

    def test_none_after_returns_empty(self):
        assert (
            _dependency_bumps_for_file(before="x", after=None, manifest_path="pyproject.toml") == []
        )

    def test_package_json_bump(self):
        before = '{"dependencies": {"react": "^17.0.0"}}'
        after = '{"dependencies": {"react": "^18.0.0"}}'
        bumps = _dependency_bumps_for_file(before=before, after=after, manifest_path="package.json")
        assert len(bumps) == 1
        assert bumps[0].name == "react"
        assert bumps[0].from_version == "^17.0.0"
        assert bumps[0].to_version == "^18.0.0"

    def test_unknown_manifest_returns_empty(self):
        bumps = _dependency_bumps_for_file(before="x", after="y", manifest_path="Makefile")
        assert bumps == []
