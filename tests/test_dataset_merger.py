"""
tests/test_dataset_merger.py
Unit tests for the dataset merging engine (src/dataset_merger.py).
"""

import json
import os
import pytest
from src.dataset_merger import (
    merge_card_stats,
    merge_color_ratings,
    merge_datasets,
    merge_dataset_files,
    auto_merge_local_draft_datasets,
)


def test_merge_card_stats_mathematical_precision():
    stats_a = {
        "samples": 1000,
        "game_count": 2000,
        "seen_count": 3000,
        "pick_count": 500,
        "gihwr": 60.0,
        "gpwr": 55.0,
        "ohwr": 58.0,
        "gdwr": 62.0,
        "gnswr": 50.0,
        "iwd": 10.0,
        "alsa": 2.50,
        "ata": 3.00,
        "play_rate": 80.0,
    }
    stats_b = {
        "samples": 500,
        "game_count": 1000,
        "seen_count": 1500,
        "pick_count": 250,
        "gihwr": 54.0,
        "gpwr": 52.0,
        "ohwr": 52.0,
        "gdwr": 56.0,
        "gnswr": 48.0,
        "iwd": 6.0,
        "alsa": 3.10,
        "ata": 3.60,
        "play_rate": 70.0,
    }

    merged = merge_card_stats(stats_a, stats_b)

    # Count summations
    assert merged["samples"] == 1500
    assert merged["game_count"] == 3000
    assert merged["seen_count"] == 4500
    assert merged["pick_count"] == 750

    # Weighted GIHWR: (60.0 * 1000 + 54.0 * 500) / 1500 = (60000 + 27000) / 1500 = 87000 / 1500 = 58.0
    assert merged["gihwr"] == 58.0

    # Weighted GPWR: (55.0 * 2000 + 52.0 * 1000) / 3000 = (110000 + 52000) / 3000 = 54.0
    assert merged["gpwr"] == 54.0

    # Weighted ALSA: (2.50 * 3000 + 3.10 * 1500) / 4500 = (7500 + 4650) / 4500 = 12150 / 4500 = 2.70
    assert merged["alsa"] == 2.70

    # Weighted ATA: (3.00 * 500 + 3.60 * 250) / 750 = (1500 + 900) / 750 = 2400 / 750 = 3.20
    assert merged["ata"] == 3.20

    # Weighted Play Rate: (80.0 * 500 + 70.0 * 250) / 750 = (40000 + 17500) / 750 = 76.67
    assert merged["play_rate"] == 76.67


def test_merge_card_stats_single_sided():
    stats_a = {
        "samples": 100,
        "game_count": 200,
        "seen_count": 300,
        "pick_count": 50,
        "gihwr": 55.5,
    }
    # Dataset B is empty
    merged_1 = merge_card_stats(stats_a, {})
    assert merged_1["samples"] == 100
    assert merged_1["gihwr"] == 55.5

    # Dataset A is None
    merged_2 = merge_card_stats(None, stats_a)
    assert merged_2["samples"] == 100
    assert merged_2["gihwr"] == 55.5


def test_merge_color_ratings():
    colors_a = {"WU": 56.0, "UB": 54.0, "BR": 50.0}
    colors_b = {"WU": 50.0, "UB": 58.0, "RG": 55.0}

    # Weight A = 3000 games, Weight B = 1000 games
    merged = merge_color_ratings(colors_a, colors_b, 3000, 1000)

    # WU: (56.0 * 3000 + 50.0 * 1000) / 4000 = (168000 + 50000) / 4000 = 54.5
    assert merged["WU"] == 54.5

    # UB: (54.0 * 3000 + 58.0 * 1000) / 4000 = (162000 + 58000) / 4000 = 55.0
    assert merged["UB"] == 55.0

    # BR exists only in A -> 50.0
    assert merged["BR"] == 50.0

    # RG exists only in B -> 55.0
    assert merged["RG"] == 55.0


def test_merge_datasets_structure():
    primary = {
        "meta": {
            "collection_date": "2026-10-17 14:00:00",
            "start_date": "2026-09-29",
            "end_date": "2026-10-17",
            "version": 3.0,
            "game_count": 5000,
            "format": "PremierDraft",
        },
        "color_ratings": {"UB": 55.0},
        "card_ratings": {
            "Card A": {
                "name": "Card A",
                "types": ["Creature"],
                "deck_colors": {
                    "All Decks": {"samples": 100, "game_count": 200, "gihwr": 60.0}
                },
            }
        },
    }

    secondary = {
        "meta": {
            "collection_date": "2026-10-17 14:30:00",
            "start_date": "2026-10-01",
            "end_date": "2026-10-17",
            "version": 3.0,
            "game_count": 1000,
            "format": "ContenderDraft",
        },
        "color_ratings": {"UB": 50.0},
        "card_ratings": {
            "Card A": {
                "name": "Card A",
                "types": ["Creature"],
                "deck_colors": {
                    "All Decks": {"samples": 50, "game_count": 100, "gihwr": 54.0}
                },
            },
            "Card B": {
                "name": "Card B",
                "types": ["Instant"],
                "deck_colors": {
                    "All Decks": {"samples": 20, "game_count": 40, "gihwr": 45.0}
                },
            },
        },
    }

    merged = merge_datasets(primary, secondary, merged_event_name="Draft")

    # Meta
    assert merged["meta"]["game_count"] == 6000
    assert merged["meta"]["start_date"] == "2026-09-29"
    assert "PremierDraft" in merged["meta"]["merged_from"]
    assert "ContenderDraft" in merged["meta"]["merged_from"]

    # Both cards present
    assert "Card A" in merged["card_ratings"]
    assert "Card B" in merged["card_ratings"]

    # Card A pooled
    card_a_stats = merged["card_ratings"]["Card A"]["deck_colors"]["All Decks"]
    assert card_a_stats["samples"] == 150
    assert card_a_stats["game_count"] == 300
    assert card_a_stats["gihwr"] == 58.0


def test_merge_dataset_files_and_auto_merge(tmp_path):
    p_data = {
        "meta": {"game_count": 100, "start_date": "2026-10-01", "end_date": "2026-10-10", "version": 3.0},
        "color_ratings": {"UB": 55.0},
        "card_ratings": {},
    }
    c_data = {
        "meta": {"game_count": 50, "start_date": "2026-10-01", "end_date": "2026-10-10", "version": 3.0},
        "color_ratings": {"UB": 50.0},
        "card_ratings": {},
    }

    p_file = tmp_path / "TST_PremierDraft_All_Data.json"
    c_file = tmp_path / "TST_ContenderDraft_All_Data.json"
    out_file = tmp_path / "TST_Draft_All_Data.json"

    p_file.write_text(json.dumps(p_data), encoding="utf-8")
    c_file.write_text(json.dumps(c_data), encoding="utf-8")

    # Direct file merge
    merge_dataset_files(str(p_file), str(c_file), str(out_file))
    assert out_file.exists()

    with open(out_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["meta"]["game_count"] == 150

    # Auto merge in folder
    out_file.unlink()
    created = auto_merge_local_draft_datasets(str(tmp_path))
    assert len(created) == 1
    assert os.path.basename(created[0]) == "TST_Draft_All_Data.json"
    assert out_file.exists()

    # Re-running when up to date should make no changes
    re_created = auto_merge_local_draft_datasets(str(tmp_path))
    assert len(re_created) == 0


def test_merge_card_stats_skips_suppressed_zero_rates_and_preserves_iwd():
    """Reproduces TRK Astute Terraformer bug: Contender has suppressed gihwr/iwd=0.0
    (samples < 500) alongside valid gnswr/gpwr."""
    premier = {
        "samples": 6822,
        "game_count": 16027,
        "seen_count": 10000,
        "pick_count": 2000,
        "gihwr": 58.06,
        "gnswr": 56.67,
        "iwd": 1.4,
        "gpwr": 57.22,
        "ohwr": 59.10,
        "gdwr": 57.40,
        "alsa": 4.20,
        "ata": 4.80,
        "play_rate": 72.0,
    }
    contender = {
        "samples": 489,
        "game_count": 1068,
        "seen_count": 500,
        "pick_count": 100,
        "gihwr": 0.0,
        "gnswr": 62.74,
        "iwd": 0.0,
        "gpwr": 62.08,
        "ohwr": 0.0,
        "gdwr": 0.0,
        "alsa": 0.0,
        "ata": 0.0,
        "play_rate": 0.0,
    }

    merged = merge_card_stats(premier, contender)

    assert merged["samples"] == 7311
    assert merged["game_count"] == 17095
    # Suppressed 0.0 rates in Contender must NOT dilute Premier's valid rates
    assert merged["gihwr"] == 58.06
    assert merged["ohwr"] == 59.10
    assert merged["gdwr"] == 57.40
    assert merged["alsa"] == 4.20
    assert merged["ata"] == 4.80
    assert merged["play_rate"] == 72.0
    # Valid Contender GPWR and GNSWR are pooled by their respective game counts
    assert merged["gpwr"] == 57.52
    assert merged["gnswr"] == 57.03
    # IWD must NOT be inverted to -2.85 by 0.0-diluted GIHWR or baseline shift
    assert merged["iwd"] == 1.4


def test_merge_card_stats_iwd_avoids_simpsons_paradox():
    """When two formats have different player-skill baselines and draw rates,
    pooling within-format IWD avoids Simpson's paradox."""
    premier = {
        "samples": 1000,
        "game_count": 2000,  # 50% draw rate
        "gihwr": 56.0,
        "gnswr": 54.0,
        "iwd": 2.0,
    }
    contender = {
        "samples": 500,
        "game_count": 2000,  # 25% draw rate, +6% higher baseline WR
        "gihwr": 62.0,
        "gnswr": 60.0,
        "iwd": 2.0,
    }

    merged = merge_card_stats(premier, contender)
    assert merged["iwd"] == 2.0


def test_auto_merge_regenerates_stale_merger_version(tmp_path):
    """Merged Draft files missing current merger_version must be re-merged automatically."""
    p_data = {
        "meta": {"game_count": 100, "start_date": "2026-10-01", "end_date": "2026-10-10", "version": 3.0},
        "color_ratings": {},
        "card_ratings": {},
    }
    c_data = {
        "meta": {"game_count": 50, "start_date": "2026-10-01", "end_date": "2026-10-10", "version": 3.0},
        "color_ratings": {},
        "card_ratings": {},
    }
    p_file = tmp_path / "TRK_PremierDraft_All_Data.json"
    c_file = tmp_path / "TRK_ContenderDraft_All_Data.json"
    out_file = tmp_path / "TRK_Draft_All_Data.json"

    p_file.write_text(json.dumps(p_data), encoding="utf-8")
    c_file.write_text(json.dumps(c_data), encoding="utf-8")
    # Write a legacy merged file (newer mtime, no merger_version)
    out_file.write_text(json.dumps({"meta": {"game_count": 150}, "color_ratings": {}, "card_ratings": {}}), encoding="utf-8")

    created = auto_merge_local_draft_datasets(str(tmp_path))
    assert len(created) == 1
    assert '"merger_version": 2' in out_file.read_text(encoding="utf-8")[:1024]
