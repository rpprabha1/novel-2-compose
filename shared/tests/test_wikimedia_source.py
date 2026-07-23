import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import validate_against_schema  # noqa: E402
from shared.sources.wikimedia import WikimediaCommonsSource  # noqa: E402

_FAKE_API_RESPONSE = {
    "query": {
        "pages": {
            "123": {
                "pageid": 123,
                "index": 0,
                "imageinfo": [
                    {
                        "mime": "video/mp4",
                        "size": 1024,
                        "url": "https://upload.wikimedia.org/fake.mp4",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:fake.mp4",
                        "duration": 12.5,
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Artist": {"value": "Jane Doe"},
                        },
                    }
                ],
            }
        }
    }
}


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return _FAKE_API_RESPONSE


def test_search_result_source_field_matches_schema_enum():
    """Regression test: a real run crashed with a jsonschema ValidationError
    the first time a Wikimedia query actually returned a hit -
    candidates.schema.json's `source` enum requires "wikimedia_commons" but
    WikimediaCommonsSource.name was "wikimedia". Earlier runs never
    exercised this because their (undiversified, generic) search queries
    happened to return zero real Wikimedia matches. Mocked HTTP response per
    CLAUDE.md's fixture rule - no live API call."""
    source = WikimediaCommonsSource()
    with patch("shared.sources.wikimedia.requests.get", return_value=_FakeResponse()):
        candidates = source.search("test query", max_results=5)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "wikimedia_commons"

    payload = {
        "run_id": "test_run",
        "scene_id": "test_scene",
        "candidates_by_beat": [
            {
                "beat_id": "b001",
                "search_terms": ["test query"],
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "source": candidate.source,
                        "url": candidate.url,
                        "license": candidate.license,
                        "thumbnail_ref": candidate.thumbnail_ref,
                    }
                ],
            }
        ],
    }
    validate_against_schema(payload, "candidates.schema.json")
