"""
src/dataset_merger.py
Statistically sound dataset merging engine.
Pools two 17Lands datasets (e.g. PremierDraft and ContenderDraft)
using sample-weighted pooling across all win rates, ALSA, ATA, and game counts.
"""

import json
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


def merge_card_stats(stats_a: Dict[str, Any], stats_b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge statistical metrics for a single card under a specific color archetype.
    Rates are weighted by their underlying sample sizes.
    """
    if not stats_a and not stats_b:
        return {}
    if not stats_a:
        return dict(stats_b)
    if not stats_b:
        return dict(stats_a)

    samples_a = int(stats_a.get("samples") or 0)
    samples_b = int(stats_b.get("samples") or 0)
    total_samples = samples_a + samples_b

    games_a = int(stats_a.get("game_count") or 0)
    games_b = int(stats_b.get("game_count") or 0)
    total_games = games_a + games_b

    seen_a = int(stats_a.get("seen_count") or 0)
    seen_b = int(stats_b.get("seen_count") or 0)
    total_seen = seen_a + seen_b

    pick_a = int(stats_a.get("pick_count") or 0)
    pick_b = int(stats_b.get("pick_count") or 0)
    total_pick = pick_a + pick_b

    merged: Dict[str, Any] = {
        "samples": total_samples,
        "game_count": total_games,
        "seen_count": total_seen,
        "pick_count": total_pick,
    }

    # Helper for weighted rate
    def _weighted(rate_a, weight_a, rate_b, weight_b, total_weight, decimals=2):
        if total_weight <= 0:
            return 0.0
        val = (float(rate_a or 0.0) * weight_a + float(rate_b or 0.0) * weight_b) / total_weight
        return round(val, decimals)

    # GIHWR (weighted by GIH samples)
    merged["gihwr"] = _weighted(
        stats_a.get("gihwr"), samples_a,
        stats_b.get("gihwr"), samples_b,
        total_samples, decimals=2
    )

    # GPWR (weighted by games played)
    merged["gpwr"] = _weighted(
        stats_a.get("gpwr"), games_a,
        stats_b.get("gpwr"), games_b,
        total_games, decimals=2
    )

    # OHWR & GDWR (weighted by samples)
    merged["ohwr"] = _weighted(
        stats_a.get("ohwr"), samples_a,
        stats_b.get("ohwr"), samples_b,
        total_samples, decimals=2
    )
    merged["gdwr"] = _weighted(
        stats_a.get("gdwr"), samples_a,
        stats_b.get("gdwr"), samples_b,
        total_samples, decimals=2
    )

    # GNSWR (weighted by games not seen: game_count - samples)
    gns_a = max(0, games_a - samples_a)
    gns_b = max(0, games_b - samples_b)
    total_gns = gns_a + gns_b
    merged["gnswr"] = _weighted(
        stats_a.get("gnswr"), gns_a,
        stats_b.get("gnswr"), gns_b,
        total_gns, decimals=2
    )

    # IWD: Improvement When Drawn (GIHWR - GNSWR)
    if merged["gihwr"] > 0.0 and merged["gnswr"] > 0.0:
        merged["iwd"] = round(merged["gihwr"] - merged["gnswr"], 2)
    else:
        merged["iwd"] = _weighted(
            stats_a.get("iwd"), total_samples,
            stats_b.get("iwd"), total_samples,
            total_samples * 2 if total_samples > 0 else 0, decimals=2
        )

    # ALSA (weighted by seen_count)
    merged["alsa"] = _weighted(
        stats_a.get("alsa"), seen_a,
        stats_b.get("alsa"), seen_b,
        total_seen, decimals=2
    )

    # ATA (weighted by pick_count)
    merged["ata"] = _weighted(
        stats_a.get("ata"), pick_a,
        stats_b.get("ata"), pick_b,
        total_pick, decimals=2
    )

    # Play Rate (weighted by pick_count if available, otherwise game_count)
    pr_weight_a = pick_a if pick_a > 0 else games_a
    pr_weight_b = pick_b if pick_b > 0 else games_b
    total_pr_weight = pr_weight_a + pr_weight_b
    merged["play_rate"] = _weighted(
        stats_a.get("play_rate"), pr_weight_a,
        stats_b.get("play_rate"), pr_weight_b,
        total_pr_weight, decimals=2
    )

    return merged


def merge_color_ratings(
    colors_a: Dict[str, float],
    colors_b: Dict[str, float],
    games_a: int,
    games_b: int,
) -> Dict[str, float]:
    """
    Merge format-level color archetype win ratings weighted by total event game counts.
    """
    total_games = games_a + games_b
    all_colors = set(colors_a.keys()) | set(colors_b.keys())
    merged_ratings: Dict[str, float] = {}

    for c in sorted(all_colors):
        rate_a = float(colors_a.get(c, 0.0) or 0.0)
        rate_b = float(colors_b.get(c, 0.0) or 0.0)

        # If one format has 0% recorded, fall back to the valid format's rate
        if rate_a > 0.0 and rate_b > 0.0 and total_games > 0:
            val = (rate_a * games_a + rate_b * games_b) / total_games
        elif rate_a > 0.0:
            val = rate_a
        elif rate_b > 0.0:
            val = rate_b
        else:
            val = 0.0

        merged_ratings[c] = round(val, 1)

    return merged_ratings


def merge_datasets(
    primary: Dict[str, Any],
    secondary: Dict[str, Any],
    merged_event_name: str = "Draft",
) -> Dict[str, Any]:
    """
    Merge two complete 17Lands dataset dictionaries (e.g. PremierDraft and ContenderDraft).
    Primary dataset metadata and card definitions take precedence where identical.
    """
    meta_p = primary.get("meta", {})
    meta_s = secondary.get("meta", {})

    games_p = int(meta_p.get("game_count", 0))
    games_s = int(meta_s.get("game_count", 0))
    total_games = games_p + games_s

    # Dates
    start_dates = [d for d in [meta_p.get("start_date"), meta_s.get("start_date")] if d]
    end_dates = [d for d in [meta_p.get("end_date"), meta_s.get("end_date")] if d]
    coll_dates = [d for d in [meta_p.get("collection_date"), meta_s.get("collection_date")] if d]

    start_date = min(start_dates) if start_dates else ""
    end_date = max(end_dates) if end_dates else ""
    collection_date = max(coll_dates) if coll_dates else ""

    merged_meta = {
        "collection_date": collection_date,
        "start_date": start_date,
        "end_date": end_date,
        "version": max(float(meta_p.get("version", 3.0)), float(meta_s.get("version", 3.0))),
        "game_count": total_games,
        "merged_from": [
            meta_p.get("format", "PremierDraft"),
            meta_s.get("format", "ContenderDraft"),
        ],
    }

    # Color Ratings
    merged_color_ratings = merge_color_ratings(
        primary.get("color_ratings", {}),
        secondary.get("color_ratings", {}),
        games_p,
        games_s,
    )

    # Card Ratings
    cards_p = primary.get("card_ratings", {})
    cards_s = secondary.get("card_ratings", {})
    all_card_names = set(cards_p.keys()) | set(cards_s.keys())

    merged_card_ratings: Dict[str, Any] = {}

    for name in all_card_names:
        c_p = cards_p.get(name)
        c_s = cards_s.get(name)

        # Base card metadata template (prefer primary)
        base = dict(c_p or c_s)
        colors_p = c_p.get("deck_colors", {}) if c_p else {}
        colors_s = c_s.get("deck_colors", {}) if c_s else {}

        all_deck_colors = set(colors_p.keys()) | set(colors_s.keys())
        merged_deck_colors: Dict[str, Any] = {}

        for col in all_deck_colors:
            st_p = colors_p.get(col, {})
            st_s = colors_s.get(col, {})
            merged_deck_colors[col] = merge_card_stats(st_p, st_s)

        base["deck_colors"] = merged_deck_colors
        merged_card_ratings[name] = base

    return {
        "meta": merged_meta,
        "color_ratings": merged_color_ratings,
        "card_ratings": merged_card_ratings,
    }


def merge_dataset_files(
    primary_path: str,
    secondary_path: str,
    output_path: str,
    merged_event_name: str = "Draft",
) -> str:
    """
    Read two JSON dataset files, merge them, and save to output_path atomically.
    """
    with open(primary_path, "r", encoding="utf-8") as f:
        primary_data = json.load(f)
    with open(secondary_path, "r", encoding="utf-8") as f:
        secondary_data = json.load(f)

    merged = merge_datasets(primary_data, secondary_data, merged_event_name=merged_event_name)

    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, output_path)

    logger.info(f"Successfully generated merged dataset: {output_path}")
    return output_path


def auto_merge_local_draft_datasets(sets_folder: str = None) -> list[str]:
    """
    Scan the local Sets folder and automatically merge PremierDraft and ContenderDraft files
    if a merged Draft file is missing or outdated.
    """
    from src.constants import SETS_FOLDER

    folder = sets_folder or SETS_FOLDER
    if not os.path.exists(folder):
        return []

    created_or_updated = []
    # Find sets that have files in folder
    all_files = os.listdir(folder)
    candidate_sets = set()
    for f in all_files:
        if "_PremierDraft_" in f:
            parts = f.split("_")
            if parts:
                candidate_sets.add(parts[0].upper())

    for set_code in candidate_sets:
        for group in ["All", "Top"]:
            p_file = os.path.join(folder, f"{set_code}_PremierDraft_{group}_Data.json")
            c_file = os.path.join(folder, f"{set_code}_ContenderDraft_{group}_Data.json")
            out_file = os.path.join(folder, f"{set_code}_Draft_{group}_Data.json")

            if os.path.exists(p_file) and os.path.exists(c_file):
                p_mtime = os.path.getmtime(p_file)
                c_mtime = os.path.getmtime(c_file)
                latest_src_mtime = max(p_mtime, c_mtime)

                needs_merge = True
                if os.path.exists(out_file):
                    out_mtime = os.path.getmtime(out_file)
                    if out_mtime >= latest_src_mtime:
                        needs_merge = False

                if needs_merge:
                    try:
                        merge_dataset_files(p_file, c_file, out_file, merged_event_name="Draft")
                        created_or_updated.append(out_file)
                    except Exception as err:
                        logger.error(f"Failed to auto-merge {set_code} ({group}): {err}")

    return created_or_updated

