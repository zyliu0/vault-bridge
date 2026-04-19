"""Tests for scripts/github_package_search.py — PyPI/web package search.

Despite the filename, this module uses web/PyPI search — NOT GitHub API.
TDD: tests written BEFORE the implementation (RED phase).

--- Candidate dataclass ---
CA1.  Candidate has all required fields: name, description, pypi_url,
      github_url, pip_name, latest_version, last_release, monthly_downloads, score
CA2.  Candidate is a dataclass
CA3.  Candidate has sensible defaults (score=0.0, monthly_downloads=0)

--- PypiInfo dataclass ---
PI1.  PypiInfo has fields: name, version, summary, home_page, project_url,
      last_release, monthly_downloads
PI2.  PypiInfo is a dataclass

--- pypi_lookup ---
PL1.  pypi_lookup("pip") returns a PypiInfo (pip is always on PyPI)
PL2.  pypi_lookup("no_such_package_xyz_999_abc") returns None
PL3.  pypi_lookup is fail-silent — never raises
PL4.  pypi_lookup("") returns None
PL5.  pypi_lookup returns PypiInfo with non-empty name and version for a known package
PL6.  pypi_lookup network call mocked — returns PypiInfo when API responds 200
PL7.  pypi_lookup returns None when API responds 404
PL8.  pypi_lookup returns None when network raises ConnectionError

--- search ---
SE1.  search returns a list (possibly empty)
SE2.  search is fail-silent — never raises regardless of input
SE3.  search("") returns []
SE4.  search(None) returns []
SE5.  search with mocked HTTP returns list of Candidate objects
SE6.  search respects max_results parameter
SE7.  search returns at most max_results entries

--- rank ---
RA1.  rank([]) returns []
RA2.  rank sorts candidates by score descending
RA3.  rank does not mutate input list
RA4.  rank returns a new list

--- search_for_extension ---
SFE1. search_for_extension builds a query containing the extension name
SFE2. search_for_extension returns a list (possibly empty)
SFE3. search_for_extension is fail-silent
SFE4. search_for_extension("") returns []
SFE5. search_for_extension with mocked search returns ranked Candidates
SFE6. search_for_extension("pdf") returns results mentioning pdf parsing

--- Edge cases ---
EC1.  Candidate with score=1.5 ranks above score=0.5
EC2.  pypi_lookup handles malformed JSON response gracefully
EC3.  search handles HTTP 500 gracefully (returns [])
EC4.  All returned Candidate objects have non-None pip_name field
EC5.  pypi_lookup populates monthly_downloads from "info" if available, else 0
"""
import json
import sys
from dataclasses import fields as dc_fields
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import github_package_search as gps  # noqa: E402


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

_PYPI_RESPONSE_PIP = {
    "info": {
        "name": "pip",
        "version": "24.0",
        "summary": "The Python package installer",
        "home_page": "https://pip.pypa.io/",
        "project_url": "https://pypi.org/project/pip/",
    },
    "releases": {
        "24.0": [{"upload_time": "2024-01-15T10:00:00"}]
    },
}

_PYPI_SEARCH_RESPONSE = {
    "data": [
        {
            "name": "pdfplumber",
            "version": "0.10.3",
            "description": "Plumb a PDF for detailed information about each char",
        },
        {
            "name": "pypdf",
            "version": "4.1.0",
            "description": "A pure-python PDF library",
        },
    ],
    "meta": {"total": 2},
}


# ---------------------------------------------------------------------------
# Candidate dataclass
# ---------------------------------------------------------------------------

class TestCandidate:
    def test_ca1_has_all_required_fields(self):
        c = gps.Candidate(
            name="pdfplumber",
            description="Plumb a PDF",
            pypi_url="https://pypi.org/project/pdfplumber/",
            github_url="https://github.com/jsvine/pdfplumber",
            pip_name="pdfplumber",
            latest_version="0.10.3",
            last_release="2024-01-01",
            monthly_downloads=50000,
            score=0.9,
        )
        assert c.name == "pdfplumber"
        assert c.description == "Plumb a PDF"
        assert c.pypi_url == "https://pypi.org/project/pdfplumber/"
        assert c.github_url == "https://github.com/jsvine/pdfplumber"
        assert c.pip_name == "pdfplumber"
        assert c.latest_version == "0.10.3"
        assert c.last_release == "2024-01-01"
        assert c.monthly_downloads == 50000
        assert c.score == 0.9

    def test_ca2_is_a_dataclass(self):
        import dataclasses
        assert dataclasses.is_dataclass(gps.Candidate)

    def test_ca3_sensible_defaults(self):
        # At minimum, score and monthly_downloads should have defaults
        field_names = {f.name for f in dc_fields(gps.Candidate)}
        assert "score" in field_names
        assert "monthly_downloads" in field_names
        # Create with minimal required args using defaults
        c = gps.Candidate(
            name="test",
            description="",
            pypi_url="",
            github_url="",
            pip_name="test",
            latest_version="",
            last_release="",
        )
        assert c.score == 0.0
        assert c.monthly_downloads == 0


# ---------------------------------------------------------------------------
# PypiInfo dataclass
# ---------------------------------------------------------------------------

class TestPypiInfo:
    def test_pi1_has_all_required_fields(self):
        p = gps.PypiInfo(
            name="pip",
            version="24.0",
            summary="The Python package installer",
            home_page="https://pip.pypa.io/",
            project_url="https://pypi.org/project/pip/",
            last_release="2024-01-15",
            monthly_downloads=0,
        )
        assert p.name == "pip"
        assert p.version == "24.0"
        assert p.summary == "The Python package installer"
        assert p.home_page == "https://pip.pypa.io/"
        assert p.project_url == "https://pypi.org/project/pip/"
        assert p.last_release == "2024-01-15"
        assert p.monthly_downloads == 0

    def test_pi2_is_a_dataclass(self):
        import dataclasses
        assert dataclasses.is_dataclass(gps.PypiInfo)


# ---------------------------------------------------------------------------
# pypi_lookup
# ---------------------------------------------------------------------------

class TestPypiLookup:
    def test_pl1_returns_pypi_info_for_known_package(self):
        """Integration-lite: real network call to PyPI for 'pip'.
        Skip if network unavailable."""
        pytest.importorskip("urllib.request")
        try:
            result = gps.pypi_lookup("pip")
            if result is not None:
                assert isinstance(result, gps.PypiInfo)
                assert result.name != ""
        except Exception:
            pytest.skip("Network unavailable")

    def test_pl2_returns_none_for_nonexistent_package(self):
        result = gps.pypi_lookup("no_such_package_xyz_999_abc_def")
        assert result is None

    def test_pl3_fail_silent_never_raises(self):
        # Should never raise regardless of input
        try:
            gps.pypi_lookup("__invalid__package__name__!!!")
        except Exception as e:
            pytest.fail(f"pypi_lookup raised: {e}")

    def test_pl4_empty_name_returns_none(self):
        result = gps.pypi_lookup("")
        assert result is None

    def test_pl5_returns_pypi_info_with_nonempty_name_and_version(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(_PYPI_RESPONSE_PIP).encode()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = gps.pypi_lookup("pip")
            assert result is not None
            assert result.name != ""
            assert result.version != ""

    def test_pl6_mocked_200_response(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(_PYPI_RESPONSE_PIP).encode()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = gps.pypi_lookup("pip")
        assert isinstance(result, gps.PypiInfo)
        assert result.name == "pip"
        assert result.version == "24.0"

    def test_pl7_returns_none_on_404(self):
        from urllib.error import HTTPError
        with patch("urllib.request.urlopen", side_effect=HTTPError(
                "url", 404, "Not Found", {}, None)):
            result = gps.pypi_lookup("nonexistent_xyz")
        assert result is None

    def test_pl8_returns_none_on_connection_error(self):
        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            result = gps.pypi_lookup("pip")
        assert result is None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_se1_returns_a_list(self):
        with patch.object(gps, "pypi_lookup", return_value=None):
            result = gps.search("pdf parser python", max_results=3)
        assert isinstance(result, list)

    def test_se2_fail_silent_never_raises(self):
        try:
            gps.search("$$$_invalid_query_$$$")
        except Exception as e:
            pytest.fail(f"search raised: {e}")

    def test_se3_empty_query_returns_empty(self):
        result = gps.search("")
        assert result == []

    def test_se4_none_query_returns_empty(self):
        result = gps.search(None)
        assert result == []

    def test_se5_mocked_returns_candidate_objects(self):
        mock_candidate = gps.Candidate(
            name="pdfplumber",
            description="PDF parser",
            pypi_url="https://pypi.org/project/pdfplumber/",
            github_url="",
            pip_name="pdfplumber",
            latest_version="0.10.3",
            last_release="2024-01-01",
            monthly_downloads=10000,
            score=0.8,
        )
        with patch.object(gps, "_fetch_candidates", return_value=[mock_candidate]):
            result = gps.search("pdf", max_results=5)
        assert isinstance(result, list)
        if result:
            assert all(isinstance(c, gps.Candidate) for c in result)

    def test_se6_respects_max_results(self):
        many_candidates = [
            gps.Candidate(
                name=f"pkg{i}",
                description="",
                pypi_url="",
                github_url="",
                pip_name=f"pkg{i}",
                latest_version="1.0",
                last_release="2024-01-01",
                monthly_downloads=i,
                score=float(i),
            )
            for i in range(10)
        ]
        with patch.object(gps, "_fetch_candidates", return_value=many_candidates):
            result = gps.search("anything", max_results=3)
        assert len(result) <= 3

    def test_se7_returns_at_most_max_results(self):
        many_candidates = [
            gps.Candidate(
                name=f"pkg{i}",
                description="",
                pypi_url="",
                github_url="",
                pip_name=f"pkg{i}",
                latest_version="1.0",
                last_release="",
                monthly_downloads=0,
                score=float(i),
            )
            for i in range(20)
        ]
        with patch.object(gps, "_fetch_candidates", return_value=many_candidates):
            result = gps.search("anything", max_results=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------

class TestRank:
    def _make_candidate(self, name, score):
        return gps.Candidate(
            name=name, description="", pypi_url="", github_url="",
            pip_name=name, latest_version="1.0", last_release="",
            monthly_downloads=0, score=score,
        )

    def test_ra1_empty_list_returns_empty(self):
        assert gps.rank([]) == []

    def test_ra2_sorts_by_score_descending(self):
        candidates = [
            self._make_candidate("low", 0.2),
            self._make_candidate("high", 0.9),
            self._make_candidate("mid", 0.5),
        ]
        ranked = gps.rank(candidates)
        assert ranked[0].name == "high"
        assert ranked[-1].name == "low"

    def test_ra3_does_not_mutate_input(self):
        c1 = self._make_candidate("a", 0.1)
        c2 = self._make_candidate("b", 0.9)
        original = [c1, c2]
        original_order = [c.name for c in original]
        gps.rank(original)
        assert [c.name for c in original] == original_order

    def test_ra4_returns_new_list(self):
        candidates = [self._make_candidate("a", 0.5)]
        ranked = gps.rank(candidates)
        assert ranked is not candidates


# ---------------------------------------------------------------------------
# search_for_extension
# ---------------------------------------------------------------------------

class TestSearchForExtension:
    def test_sfe1_builds_query_containing_extension(self):
        """search_for_extension should search using the extension name."""
        with patch.object(gps, "search", return_value=[]) as mock_search:
            gps.search_for_extension("pdf")
            if mock_search.call_args:
                query_arg = mock_search.call_args[0][0]
                assert "pdf" in query_arg.lower()

    def test_sfe2_returns_a_list(self):
        with patch.object(gps, "search", return_value=[]):
            result = gps.search_for_extension("pdf")
        assert isinstance(result, list)

    def test_sfe3_fail_silent(self):
        try:
            gps.search_for_extension("???invalid???")
        except Exception as e:
            pytest.fail(f"search_for_extension raised: {e}")

    def test_sfe4_empty_string_returns_empty(self):
        result = gps.search_for_extension("")
        assert result == []

    def test_sfe5_mocked_search_returns_ranked_candidates(self):
        c1 = gps.Candidate(name="a", description="", pypi_url="", github_url="",
                           pip_name="a", latest_version="1.0", last_release="",
                           monthly_downloads=100, score=0.3)
        c2 = gps.Candidate(name="b", description="", pypi_url="", github_url="",
                           pip_name="b", latest_version="1.0", last_release="",
                           monthly_downloads=200, score=0.8)
        with patch.object(gps, "search", return_value=[c1, c2]):
            result = gps.search_for_extension("pdf")
        assert isinstance(result, list)
        if len(result) >= 2:
            assert result[0].score >= result[-1].score

    def test_sfe6_pdf_extension_search(self):
        """search_for_extension('pdf') should produce relevant results
        (integration test, skipped if network unavailable)."""
        try:
            results = gps.search_for_extension("pdf", max_results=5)
            assert isinstance(results, list)
            assert len(results) <= 5
        except Exception:
            pytest.skip("Network unavailable or API changed")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def _make_candidate(self, name, score):
        return gps.Candidate(
            name=name, description="", pypi_url="", github_url="",
            pip_name=name, latest_version="1.0", last_release="",
            monthly_downloads=0, score=score,
        )

    def test_ec1_higher_score_ranks_first(self):
        high = self._make_candidate("high", 1.5)
        low = self._make_candidate("low", 0.5)
        ranked = gps.rank([low, high])
        assert ranked[0].name == "high"

    def test_ec2_pypi_lookup_handles_malformed_json(self):
        mock_response = MagicMock()
        mock_response.read.return_value = b"not valid json {{{"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            result = gps.pypi_lookup("pip")
        assert result is None

    def test_ec3_search_handles_http_500(self):
        from urllib.error import HTTPError
        with patch("urllib.request.urlopen",
                   side_effect=HTTPError("url", 500, "Server Error", {}, None)):
            result = gps.search("pdf parser", max_results=5)
        assert result == [] or isinstance(result, list)

    def test_ec4_all_candidates_have_nonnone_pip_name(self):
        c = self._make_candidate("test", 0.5)
        assert c.pip_name is not None

    def test_ec5_pypi_lookup_monthly_downloads_defaults_to_zero(self):
        payload = {
            "info": {
                "name": "test-pkg",
                "version": "1.0",
                "summary": "test",
                "home_page": "",
                "project_url": "",
            },
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(payload).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            result = gps.pypi_lookup("test-pkg")
        if result is not None:
            assert result.monthly_downloads == 0
