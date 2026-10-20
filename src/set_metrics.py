import statistics as stats
from typing import Tuple
from pydantic import BaseModel
from src.dataset import Dataset
from src.constants import (
    DECK_COLORS,
    DATA_FIELD_NAME,
    DATA_FIELD_DECK_COLORS,
    WIN_RATE_OPTIONS,
)


class ColorMetrics(BaseModel):
    mean: float = 0.0
    std: float = 0.0
    count: int = 0  # distinct cards with a non-zero value for this color/field


class SetMetrics:
    """
    This class is used to calculate the mean, standard deviation for a MTG set dataset.
    """

    def __init__(self, dataset: Dataset, digits: int = 2):
        self._color_metrics: dict = {}
        self._digits: int = digits
        from src.constants import CARD_COLORS
        self.format_texture: dict = {
            c: {
                "removal": 0,
                "2-drop": 0,
                "evasion": 0,
                "fixing_ramp": 0,
                "card_advantage": 0,
            }
            for c in CARD_COLORS
        }
        self._precompute_all(dataset)

    def _build_format_texture(self, dataset: Dataset) -> None:
        pass  # Handled inside _precompute_all

    def get_metrics(self, color: str, field: str) -> Tuple[float, float]:
        """
        Get the mean and standard deviation metrics for a specific color and field.
        """
        mean, std = (0.0, 0.0)

        if field in self._color_metrics and color in self._color_metrics[field]:
            mean = self._color_metrics[field][color].mean
            std = self._color_metrics[field][color].std

        return mean, std

    def get_sample_count(self, color: str, field: str) -> int:
        """Number of distinct cards that contributed to get_metrics(color, field)."""
        if field in self._color_metrics and color in self._color_metrics[field]:
            return self._color_metrics[field][color].count
        return 0

    def calculate_percentile(self, winrate: float, colors: str, field: str) -> float:
        """
        Calculate the percentile for a given win rate, color, and field using the cumulative distribution function (CDF) of a normal distribution.
        """
        mean, std = self.get_metrics(colors, field)

        percentile = round(stats.NormalDist(mu=mean, sigma=std).cdf(winrate) * 100, 2)

        return percentile

    def generate_metrics(self, dataset: Dataset) -> None:
        self._precompute_all(dataset)

    def _precompute_all(self, dataset: Dataset) -> None:
        """
        Calculate mean/std and format texture in a single deduplicated pass.
        """
        if not dataset:
            return

        dataset_dict = dataset.get_card_ratings()
        if not dataset_dict:
            return

        values_by_field_color = {
            field: {color: [] for color in DECK_COLORS}
            for field in WIN_RATE_OPTIONS
        }

        from src.utils import normalize_color_string

        processed_cards = set()
        unique_card_list = []
        for card_data in dataset_dict.values():
            card_name = card_data.get(DATA_FIELD_NAME)
            if not card_name or card_name in processed_cards:
                continue
            processed_cards.add(card_name)
            unique_card_list.append(card_data)

            deck_stats = card_data.get(DATA_FIELD_DECK_COLORS, {})
            if not deck_stats:
                continue

            for color in DECK_COLORS:
                std_color = normalize_color_string(color)
                color_stats = deck_stats.get(std_color)
                if not color_stats:
                    continue

                for field in WIN_RATE_OPTIONS:
                    val = color_stats.get(field)
                    if val is not None and val != 0.0:
                        values_by_field_color[field][color].append(round(val, self._digits))

        for field in WIN_RATE_OPTIONS:
            self._color_metrics[field] = {}
            for color in DECK_COLORS:
                metrics = ColorMetrics()
                vals = values_by_field_color[field][color]
                if vals:
                    metrics.count = len(vals)
                    metrics.mean = round(stats.mean(vals), self._digits)
                    metrics.std = round(stats.pstdev(vals), self._digits)
                self._color_metrics[field][color] = metrics

        # Single-pass texture calculation using the already-deduplicated card list
        baseline_wr, std = self.get_metrics("All Decks", "gihwr")
        if baseline_wr > 0.0:
            playable_threshold = baseline_wr - std
            for card in unique_card_list:
                rarity = str(card.get("rarity", "common")).lower()
                if rarity not in ["common", "uncommon"]:
                    continue
                gihwr = float(
                    card.get("deck_colors", {}).get("All Decks", {}).get("gihwr", 0.0)
                )
                if gihwr < playable_threshold:
                    continue
                colors = card.get("colors", [])
                if not colors or len(colors) > 2:
                    continue
                tags = card.get("tags", [])
                cmc = int(card.get("cmc", 0))
                types = card.get("types", [])
                is_2_drop = "Creature" in types and cmc <= 2

                for color in colors:
                    if color not in self.format_texture:
                        continue
                    if is_2_drop:
                        self.format_texture[color]["2-drop"] += 1
                    if "removal" in tags:
                        self.format_texture[color]["removal"] += 1
                    if "evasion" in tags:
                        self.format_texture[color]["evasion"] += 1
                    if "fixing_ramp" in tags:
                        self.format_texture[color]["fixing_ramp"] += 1
                    if "card_advantage" in tags:
                        self.format_texture[color]["card_advantage"] += 1

    def generate_color_metrics(
        self, color: str, field: str, dataset: Dataset
    ) -> ColorMetrics:
        """
        Calculate the mean and standard deviation for a specific color and field
        """
        metrics = ColorMetrics()

        processed_cards = set()
        unique_gihwr = []

        # Use a localized variable to avoid masking the dataset argument
        dataset_dict = dataset.get_card_ratings()

        if not dataset_dict:
            return metrics

        from src.utils import normalize_color_string

        std_color = normalize_color_string(color)

        # Iterate over the card list and retrieve the GIHWR for unique cards
        for card_data in dataset_dict.values():
            card_name = card_data.get(DATA_FIELD_NAME)
            if not card_name:
                continue

            # O(1) hash lookup prevents fatal hangs when processing massive Day 1 datasets
            if card_name not in processed_cards:
                processed_cards.add(card_name)

                deck_stats = card_data.get(DATA_FIELD_DECK_COLORS, {})
                if std_color not in deck_stats:
                    continue

                color_stats = deck_stats[std_color]
                if field not in color_stats:
                    continue

                val = color_stats[field]
                # Only include valid data points to avoid calculating empty metrics
                if val != 0.0:
                    unique_gihwr.append(round(val, self._digits))

        if not unique_gihwr:
            return metrics

        metrics.count = len(unique_gihwr)
        metrics.mean = round(stats.mean(unique_gihwr), self._digits)
        metrics.std = round(stats.pstdev(unique_gihwr), self._digits)

        return metrics
