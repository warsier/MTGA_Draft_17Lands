import pytest
import json
from unittest.mock import MagicMock, patch
from src.file_extractor import FileExtractor
from src.utils import Result


@patch("src.file_extractor.os.path.exists", return_value=True)
@patch("src.file_extractor.open", new_callable=MagicMock)
def test_retrieve_stored_data(mock_open, mock_exists):
    """Verify the file extractor can read and filter the temporary card database."""
    from src.file_extractor import FileExtractor
    import json

    extractor = FileExtractor(None, MagicMock(), MagicMock(), MagicMock())

    # Mock the JSON content
    mock_data = {"M10": {"1001": {"name": "Bolt"}}, "OTJ": {"2002": {"name": "Snake"}}}

    # Setup mock file to return JSON
    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(
        mock_data
    )

    # Requesting a specific set
    res = extractor._retrieve_stored_data(["M10"])
    assert res is True
    assert "1001" in extractor.card_dict
    assert extractor.card_dict["1001"]["name"] == "Bolt"
    assert extractor.card_dict["1001"]["set"] == "M10"
    assert "2002" not in extractor.card_dict

    # Requesting ALL sets
    extractor.card_dict = {}
    res2 = extractor._retrieve_stored_data(["ALL"])
    assert res2 is True
    assert "1001" in extractor.card_dict
    assert "2002" in extractor.card_dict


@patch("src.utils.invalidate_local_set_cache")
def test_export_card_data(mock_invalidate, tmp_path, monkeypatch):
    """Verify that dataset exports create the correctly formatted filename and invalidate the UI's fast-cache."""
    from src.file_extractor import FileExtractor

    extractor = FileExtractor(None, MagicMock(), MagicMock(), MagicMock())
    extractor.start_date = "2024-01-01"
    extractor.end_date = "2024-02-01"
    extractor.draft = "PremierDraft"
    extractor.user_group = "All"
    extractor.time_period = "LATEST_EVENT"
    extractor.selected_sets = MagicMock()
    extractor.selected_sets.seventeenlands = ["OTJ"]
    monkeypatch.setattr("src.constants.SETS_FOLDER", str(tmp_path))
    extractor.combined_data = {
        "meta": {"version": 3, "start_date": "2024-01-01", "end_date": "2024-02-01"},
        "card_ratings": {str(i): {"name": f"Card {i}"} for i in range(10)},
    }
    destination = tmp_path / "OTJ_PremierDraft_All_Custom-LatestEvent-20240201_Data.json"
    destination.write_text("previous dataset", encoding="utf-8")

    filename = extractor.export_card_data()

    # The stamp encodes the time_period preset so downloads of different
    # presets on the same day don't overwrite each other.
    assert "OTJ_PremierDraft_All_Custom-LatestEvent-20240201_Data.json" in filename
    assert json.loads(destination.read_text(encoding="utf-8")) == extractor.combined_data
    assert list(tmp_path.iterdir()) == [destination]
    mock_invalidate.assert_called_once()


@pytest.mark.parametrize("failure", ["validation", "serialization", "replace", "write"])
def test_failed_export_preserves_previous_dataset(failure, tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.SETS_FOLDER", str(tmp_path))
    extractor = FileExtractor(None, MagicMock(), MagicMock(), MagicMock())
    extractor.selected_sets = MagicMock(seventeenlands=["OTJ"])
    extractor.draft = "PremierDraft"
    extractor.user_group = "All"
    extractor.time_period = "ALL_TIME"
    extractor.end_date = "2024-02-01"
    extractor.combined_data = {
        "meta": {"version": 3},
        "card_ratings": {str(i): {"name": f"Card {i}"} for i in range(10)},
    }
    destination = tmp_path / "OTJ_PremierDraft_All_Custom-AllTime-20240201_Data.json"
    destination.write_bytes(b"previous dataset")
    if failure == "validation":
        extractor.combined_data["card_ratings"] = {}
    elif failure == "serialization":
        extractor.combined_data["invalid"] = float("nan")
    elif failure == "replace":
        monkeypatch.setattr("src.file_extractor.os.replace", MagicMock(side_effect=PermissionError))
    else:
        def fail_write(data, file, **kwargs):
            file.write('{"partial":')
            raise OSError("Disk full")
        monkeypatch.setattr("src.file_extractor.json.dump", fail_write)
    with patch("src.utils.invalidate_local_set_cache") as invalidate:
        assert extractor.export_card_data() == ""
        invalidate.assert_not_called()
    assert destination.read_bytes() == b"previous dataset"
    assert list(tmp_path.iterdir()) == [destination]


def test_search_arena_log_locations_manual():
    from src.file_extractor import search_arena_log_locations

    with patch("src.file_extractor.os.path.exists", return_value=True):
        assert search_arena_log_locations("manual.log", "config.log") == "manual.log"


def test_retrieve_arena_directory():
    from src.file_extractor import retrieve_arena_directory
    from unittest.mock import mock_open

    mock_data = (
        "Garbage line 1\n"
        "Garbage line 2\n"
        "Some log line 'C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Managed'\n"
    )
    with patch("builtins.open", mock_open(read_data=mock_data)):
        with patch("src.file_extractor.os.path.exists", return_value=True):
            with patch("sys.platform", "win32"):
                res = retrieve_arena_directory("log.txt")
                assert res == "C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data"


@patch("src.file_extractor.FileExtractor.export_card_data", return_value="output.json")
@patch("src.file_extractor.FileExtractor._inject_community_tags", return_value=[])
@patch(
    "src.file_extractor.FileExtractor._retrieve_local_arena_data",
    return_value=(True, "Success", 1024),
)
@patch("src.file_extractor.Seventeenlands.download_set_data")
def test_download_card_data_success_pipeline(
    mock_17l, mock_local_data, mock_tags, mock_export
):
    """Verify the entire file extractor pipeline processes successfully when components align."""
    extractor = FileExtractor(None, MagicMock(), MagicMock(), MagicMock())
    extractor.selected_sets = MagicMock(
        seventeenlands=["OTJ"], arena=["OTJ"], set_code="OTJ"
    )
    extractor.draft = "PremierDraft"
    extractor.start_date = "2024-01-01"
    extractor.end_date = "2024-02-01"

    # Mock local arena cards
    extractor.card_dict = {
        "123": {"name": "Lightning Bolt", "set": "OTJ"},
        "999": {
            "name": "Basic Plains",
            "set": "OTJ",
        },  # Should trigger fallback initialization
    }

    # Mock 17Lands response
    mock_17l.return_value = {
        "Lightning Bolt": {
            "deck_colors": {"All Decks": {"gihwr": 60.0}},
            "image": ["img_url"],
        }
    }

    # Force a color target
    extractor.combined_data["color_ratings"] = {"All Decks": 55.0}

    success, msg, size = extractor.download_card_data(0)

    assert success is True
    assert "Download Successful" in msg
    assert "123" in extractor.combined_data["card_ratings"]
    assert (
        extractor.combined_data["card_ratings"]["123"]["deck_colors"]["All Decks"][
            "gihwr"
        ]
        == 60.0
    )


@patch(
    "src.file_extractor.FileExtractor._retrieve_local_arena_data",
    return_value=(True, "Success", 1024),
)
@patch("src.file_extractor.Seventeenlands.download_set_data")
def test_download_card_data_api_failure_graceful_fallback(mock_17l, mock_local_data):
    """Verify that a 404 from 17Lands (e.g. Day 1 release) still saves the local card data for tooltips."""
    extractor = FileExtractor(None, MagicMock(), MagicMock(), MagicMock())
    extractor.selected_sets = MagicMock(
        seventeenlands=["OTJ"], arena=["OTJ"], set_code="OTJ"
    )

    extractor.card_dict = {"123": {"name": "New Card", "set": "OTJ"}}

    # Simulate 17Lands not having data yet
    mock_17l.side_effect = Exception("HTTP 404 Not Found")

    # We must patch export so it doesn't try to write to a real directory
    with patch(
        "src.file_extractor.FileExtractor.export_card_data", return_value="output.json"
    ), patch(
        "src.file_extractor.FileExtractor._inject_community_tags", return_value=[]
    ):
        success, msg, size = extractor.download_card_data(0)

    # App MUST return True so it saves the JSON. The UI needs this JSON for card text/tooltips!
    assert success is True
    assert "17Lands data not yet available" in msg
    assert "123" in extractor.combined_data["card_ratings"]
    # Verify card was initialized safely
    assert "All Decks" in extractor.combined_data["card_ratings"]["123"]["deck_colors"]
