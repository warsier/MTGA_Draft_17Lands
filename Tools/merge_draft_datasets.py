"""
Tools/merge_draft_datasets.py
CLI tool to merge Premier and Contender draft datasets for MTGA Draft Tool.

Usage:
    python Tools/merge_draft_datasets.py --set FRA
    python Tools/merge_draft_datasets.py --all
"""

import argparse
import glob
import os
import sys

# Ensure root directory is on sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.dataset_merger import merge_dataset_files
from src.constants import SETS_FOLDER


def merge_set_draft_data(set_code: str, sets_dir: str = SETS_FOLDER) -> list[str]:
    """Merge PremierDraft and ContenderDraft files for a specific set."""
    created = []
    set_code = set_code.upper()

    for group in ["All", "Top"]:
        p_pattern = os.path.join(sets_dir, f"{set_code}_PremierDraft_{group}_Data.json")
        c_pattern = os.path.join(sets_dir, f"{set_code}_ContenderDraft_{group}_Data.json")
        out_path = os.path.join(sets_dir, f"{set_code}_Draft_{group}_Data.json")

        if os.path.exists(p_pattern) and os.path.exists(c_pattern):
            print(f"Merging {set_code} ({group}): {os.path.basename(p_pattern)} + {os.path.basename(c_pattern)}")
            merge_dataset_files(p_pattern, c_pattern, out_path, merged_event_name="Draft")
            created.append(out_path)
        elif os.path.exists(p_pattern):
            print(f"Note: {set_code} has PremierDraft ({group}) but no ContenderDraft file.")
        elif os.path.exists(c_pattern):
            print(f"Note: {set_code} has ContenderDraft ({group}) but no PremierDraft file.")

    return created


def find_all_mergable_sets(sets_dir: str = SETS_FOLDER) -> set[str]:
    """Find all set codes that have both Premier and Contender datasets."""
    sets = set()
    if not os.path.exists(sets_dir):
        return sets

    for f in os.listdir(sets_dir):
        if "_PremierDraft_" in f or "_ContenderDraft_" in f:
            parts = f.split("_")
            if parts:
                sets.add(parts[0].upper())
    return sets


def main():
    parser = argparse.ArgumentParser(description="Merge Premier and Contender Draft datasets.")
    parser.add_argument("--set", type=str, help="Specific set code to merge (e.g. FRA, BLB)")
    parser.add_argument("--all", action="store_true", help="Merge all sets in the Sets directory")
    parser.add_argument("--sets-dir", type=str, default=SETS_FOLDER, help="Custom Sets folder path")
    args = parser.parse_args()

    if args.set:
        targets = [args.set.upper()]
    elif args.all:
        targets = sorted(list(find_all_mergable_sets(args.sets_dir)))
        if not targets:
            print(f"No Premier or Contender datasets found in {args.sets_dir}")
            return
    else:
        # Default behavior: scan and merge all eligible sets
        targets = sorted(list(find_all_mergable_sets(args.sets_dir)))
        if not targets:
            parser.print_help()
            return

    print(f"Processing {len(targets)} set(s): {', '.join(targets)}")
    total_created = 0
    for s in targets:
        created = merge_set_draft_data(s, args.sets_dir)
        total_created += len(created)

    print(f"Done! Created/updated {total_created} merged dataset file(s).")


if __name__ == "__main__":
    main()
