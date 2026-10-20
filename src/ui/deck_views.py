"""
src/ui/deck_views.py
Shared UI components and renderers for deck visualization and simulation results.
Consolidates presentation logic across Custom Deck and Suggest Deck tabs.
"""

import os
import random
import urllib.parse
from typing import List, Dict, Any, Optional, Callable
import tkinter
from tkinter import ttk
import ttkbootstrap as tb
from PIL import Image, ImageTk

from src import constants
from src.ui.styles import Theme
from src.ui.components import CardImageLoader, CardToolTip
from src.advisor.mana_base import get_strict_colors, is_castable
from src.advisor.deck_builder import get_functional_cmc


def show_sim_loading(sim_frame: ttk.Frame, msg: str = "Running 10,000 Monte Carlo Simulations...") -> None:
    """Renders the loading spinner and status message for simulation runs."""
    if not sim_frame or not sim_frame.winfo_exists():
        return
    for widget in sim_frame.winfo_children():
        widget.destroy()

    lbl = tb.Label(
        sim_frame,
        text=msg,
        font=Theme.scaled_font(10, "italic"),
        bootstyle="secondary",
        justify="center",
        wraplength=Theme.scaled_val(300),
    )
    lbl.is_dynamic_wrap = True
    lbl.pack(pady=Theme.scaled_val(20))

    progress = tb.Progressbar(sim_frame, mode="indeterminate")
    progress.pack(fill="x", padx=Theme.scaled_val(20))
    progress.start(15)


def show_sim_error(sim_frame: ttk.Frame, error: Any) -> None:
    """Renders simulation error notifications."""
    if not sim_frame or not sim_frame.winfo_exists():
        return
    for widget in sim_frame.winfo_children():
        widget.destroy()

    lbl = tb.Label(
        sim_frame,
        text=f"Simulation Error:\n{error}",
        bootstyle="danger",
        wraplength=Theme.scaled_val(300),
    )
    lbl.is_dynamic_wrap = True
    lbl.pack(pady=Theme.scaled_val(20))


def show_sim_results(
    sim_frame: ttk.Frame,
    stats: Optional[Dict[str, Any]],
    deck_list: List[Dict[str, Any]],
    sb_list: Optional[List[Dict[str, Any]]] = None,
    optimization_note: Optional[str] = None,
    sim_canvas: Optional[tkinter.Canvas] = None,
    after_fn: Optional[Callable] = None,
) -> None:
    """Renders Monte Carlo simulation results, consistency metrics, and strategic advice."""
    if not sim_frame or not sim_frame.winfo_exists():
        return

    for widget in sim_frame.winfo_children():
        widget.destroy()

    if not stats:
        tb.Label(
            sim_frame,
            text="Deck must have 40 cards to analyze.",
            bootstyle="warning",
        ).pack(pady=Theme.scaled_val(20))
        return

    sb_list = sb_list or []

    def _add_stat(label, value, thresholds, reverse=False, is_percent=True):
        frame = ttk.Frame(sim_frame)
        frame.pack(fill="x", pady=Theme.scaled_val(2))
        ttk.Label(frame, text=label, font=Theme.scaled_font(10, "bold")).pack(side="left")

        good_val, fair_val = thresholds

        if not reverse:
            if value >= good_val:
                icon, color = "🟢 Great", "success"
            elif value >= fair_val:
                icon, color = "🟡 Fair", "warning"
            else:
                icon, color = "🔴 Poor", "danger"
        else:
            if value <= good_val:
                icon, color = "🟢 Great", "success"
            elif value <= fair_val:
                icon, color = "🟡 Fair", "warning"
            else:
                icon, color = "🔴 Poor", "danger"

        val_str = f"{value:.1f}%" if is_percent else f"{value:.2f}"

        right_frame = ttk.Frame(frame)
        right_frame.pack(side="right")

        ttk.Label(
            right_frame,
            text=val_str,
            font=Theme.scaled_font(10, "bold"),
            width=6,
            anchor="e",
        ).pack(side="left", padx=Theme.scaled_val((0, 6)))

        tb.Label(
            right_frame,
            text=icon,
            font=Theme.scaled_font(10, "bold"),
            bootstyle=color,
            anchor="w",
        ).pack(side="left")

    tb.Label(
        sim_frame,
        text="CONSISTENCY METRICS",
        bootstyle="primary",
        font=Theme.scaled_font(10, "bold"),
    ).pack(anchor="w", pady=Theme.scaled_val((0, 5)))

    _add_stat("T2 Play (2-Drop):", stats.get("cast_t2", 0.0), (65, 50))
    _add_stat("T3 Play (3-Drop):", stats.get("cast_t3", 0.0), (65, 50))
    _add_stat("T4 Play (4-Drop):", stats.get("cast_t4", 0.0), (55, 40))
    _add_stat("Perfect Curve (T2-T4):", stats.get("curve_out", 0.0), (25, 15))
    _add_stat("Removal by Turn 4:", stats.get("removal_t4", 0.0), (60, 45))

    ttk.Separator(sim_frame).pack(fill="x", pady=Theme.scaled_val(8))

    tb.Label(
        sim_frame,
        text="RISK FACTORS",
        bootstyle="primary",
        font=Theme.scaled_font(10, "bold"),
    ).pack(anchor="w", pady=Theme.scaled_val((0, 5)))

    _add_stat("Mulligan Rate:", stats.get("mulligans", 0.0), (15, 25), reverse=True)
    _add_stat(
        "Avg. Hand Size:", stats.get("avg_hand_size", 7.0), (6.8, 6.5), is_percent=False
    )
    _add_stat("Missed 3rd Land Drop:", stats.get("screw_t3", 0.0), (15, 25), reverse=True)
    _add_stat("Missed 4th Land Drop:", stats.get("screw_t4", 0.0), (25, 35), reverse=True)
    _add_stat("Color Screwed (T3):", stats.get("color_screw_t3", 0.0), (6, 12), reverse=True)
    _add_stat("Mana Flooded (T5):", stats.get("flood_t5", 0.0), (20, 30), reverse=True)

    ttk.Separator(sim_frame).pack(fill="x", pady=Theme.scaled_val(8))

    # --- ADVISOR SUMMARY LOGIC ---
    tb.Label(
        sim_frame,
        text="ADVISOR SUMMARY",
        bootstyle="info",
        font=Theme.scaled_font(10, "bold"),
    ).pack(anchor="w", pady=Theme.scaled_val((0, 5)))

    if optimization_note:
        lbl_opt = tb.Label(
            sim_frame,
            text=optimization_note,
            font=Theme.scaled_font(9, "bold"),
            bootstyle="success",
        )
        lbl_opt.is_dynamic_wrap = True
        lbl_opt.pack(anchor="w", pady=Theme.scaled_val(2))

    advice = []
    if stats.get("cast_t2", 0.0) < 50:
        advice.append("• Add more 2-drops to improve early board presence.")

    non_basics = [
        c
        for c in deck_list
        if "Land" in c.get("types", [])
        and "Basic" not in c.get("types", [])
        and c.get("name") not in constants.BASIC_LANDS
    ]
    colorless_lands = [c for c in non_basics if not c.get("colors")]

    if stats.get("color_screw_t3", 0.0) > 10.0:
        if colorless_lands:
            advice.append(
                f"• Color screw risk is elevated. Consider cutting a colorless utility land (like {colorless_lands[0].get('name', '')}) for a basic land."
            )
        else:
            advice.append(
                "• High color screw risk. Consider cutting a splash card or adding more fixing."
            )

    is_18_lands = optimization_note and "18 Lands" in optimization_note
    is_16_lands = optimization_note and "16 Lands" in optimization_note

    if stats.get("screw_t3", 0.0) > 22.0 and not is_16_lands:
        advice.append(
            "• Frequently missing land drops. Consider running an extra land."
        )
    if stats.get("flood_t5", 0.0) > 28.0 and not is_18_lands:
        advice.append(
            "• High flood risk. Consider cutting a land or adding mana sinks."
        )
    if stats.get("removal_t4", 0.0) < 45:
        advice.append("• Low early interaction. Prioritize cheap removal.")

    deck_colors = set()
    for c in deck_list:
        if "Land" not in c.get("types", []):
            for col in c.get("colors", []):
                deck_colors.add(col)

    if len(deck_colors) >= 3:
        advice.append(
            "⚠️ Mana Base: You are playing 3+ colors. This inherently increases your risk of color screw. Ensure you have at least 3-4 strong fixing sources."
        )

    # Swap suggestions
    if not optimization_note:
        if stats.get("cast_t2", 0.0) < 50 or stats.get("flood_t5", 0.0) > 25:
            expensive_cards = [
                c
                for c in deck_list
                if int(c.get("cmc", 0)) >= 5 and "Land" not in c.get("types", [])
            ]
            if expensive_cards:
                deck_spells = [
                    c for c in deck_list if "Land" not in c.get("types", [])
                ]
                deck_colors_strict = (
                    get_strict_colors(deck_spells)
                    if deck_spells
                    else ["W", "U", "B", "R", "G"]
                )

                worst_expensive = min(
                    expensive_cards,
                    key=lambda x: float(
                        x.get("deck_colors", {})
                        .get("All Decks", {})
                        .get("gihwr", 0)
                    ),
                )
                cheap_sb = [
                    c
                    for c in sb_list
                    if int(c.get("cmc", 0)) <= 3
                    and "Land" not in c.get("types", [])
                    and "Creature" in c.get("types", [])
                    and is_castable(c, deck_colors_strict, strict=True)
                ]
                if cheap_sb:
                    best_cheap = max(
                        cheap_sb,
                        key=lambda x: float(
                            x.get("deck_colors", {})
                            .get("All Decks", {})
                            .get("gihwr", 0)
                        ),
                    )
                    advice.append(
                        f"• Swap: Cut [{worst_expensive['name']}] for [{best_cheap['name']}] to lower curve."
                    )

    for tip in advice:
        lbl_tip = ttk.Label(sim_frame, text=tip, font=Theme.scaled_font(9))
        lbl_tip.is_dynamic_wrap = True
        lbl_tip.pack(anchor="w", pady=Theme.scaled_val(2))

    # Single configure event outside the advice packing loop
    sim_frame.event_generate("<Configure>")

    if sim_canvas and sim_canvas.winfo_exists() and after_fn:
        after_fn(
            50,
            lambda: sim_canvas.configure(scrollregion=sim_canvas.bbox("all")),
        )


def render_deck_stats(
    stats_frame: ttk.Frame,
    deck_list: List[Dict[str, Any]],
    show_subtypes: bool = False,
) -> None:
    """Renders deck composition, pip requirements, roles, and mana curve."""
    if not stats_frame or not stats_frame.winfo_exists():
        return

    for widget in stats_frame.winfo_children():
        widget.destroy()

    if not deck_list:
        return

    total_cards = sum(c.get("count", 1) for c in deck_list)
    creatures = sum(
        c.get("count", 1)
        for c in deck_list
        if "Creature" in c.get("types", [])
    )
    lands = sum(
        c.get("count", 1) for c in deck_list if "Land" in c.get("types", [])
    )
    spells = total_cards - creatures - lands

    pips = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0}
    curve = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
    tags = {}
    cmc_sum = 0
    non_lands = 0

    supertypes = {
        "Creature",
        "Instant",
        "Sorcery",
        "Enchantment",
        "Artifact",
        "Planeswalker",
        "Land",
        "Legendary",
        "Basic",
        "Snow",
        "World",
        "Tribal",
        "Kindred",
        "Ongoing",
    }
    subtypes = {}

    for c in deck_list:
        count = c.get("count", 1)
        if "Land" not in c.get("types", []):
            non_lands += count
            cmc = get_functional_cmc(c)
            cmc_sum += cmc * count
            idx = min(cmc, 6)
            if idx == 0:
                idx = 1
            curve[idx] += count

            cost = c.get("mana_cost", "")
            for symbol in "WUBRG":
                pips[symbol] += cost.count(symbol) * count

            for t in c.get("tags", []):
                tags[t] = tags.get(t, 0) + count

        if show_subtypes and "Creature" in c.get("types", []):
            card_subtypes = c.get("subtypes", [])
            if card_subtypes:
                for t in card_subtypes:
                    subtypes[t] = subtypes.get(t, 0) + count
            else:
                for t in c.get("types", []):
                    if t not in supertypes:
                        subtypes[t] = subtypes.get(t, 0) + count

    avg_cmc = cmc_sum / non_lands if non_lands else 0
    top_tribes = sorted(subtypes.items(), key=lambda x: x[1], reverse=True)[:5] if show_subtypes else []

    comp_frame = ttk.Frame(stats_frame)
    comp_frame.pack(fill="x", pady=Theme.scaled_val(5))
    tb.Label(
        comp_frame,
        text="DECK COMPOSITION",
        font=Theme.scaled_font(10, "bold"),
        bootstyle="primary",
    ).pack(anchor="w")
    ttk.Label(
        comp_frame,
        text=f"Total Cards: {total_cards}  |  Creatures: {creatures}  |  Non-Creatures: {spells}  |  Lands: {lands}",
    ).pack(anchor="w", pady=Theme.scaled_val(2))

    ttk.Separator(stats_frame, orient="horizontal").pack(
        fill="x", pady=Theme.scaled_val(8)
    )

    color_frame = ttk.Frame(stats_frame)
    color_frame.pack(fill="x", pady=Theme.scaled_val(5))
    tb.Label(
        color_frame,
        text="COLOR REQUIREMENTS (PIPS)",
        font=Theme.scaled_font(10, "bold"),
        bootstyle="primary",
    ).pack(anchor="w")

    pip_str = []
    for symbol, name, _ in [
        ("W", "White", Theme.TEXT_MUTED),
        ("U", "Blue", Theme.ACCENT),
        ("B", "Black", "#8b8b93"),
        ("R", "Red", Theme.ERROR),
        ("G", "Green", Theme.SUCCESS),
    ]:
        if pips[symbol] > 0:
            pip_str.append(f"{name} ({symbol}): {pips[symbol]}")
    ttk.Label(
        color_frame, text="  |  ".join(pip_str) if pip_str else "Colorless"
    ).pack(anchor="w", pady=Theme.scaled_val(2))

    ttk.Separator(stats_frame, orient="horizontal").pack(
        fill="x", pady=Theme.scaled_val(8)
    )

    tags_frame = ttk.Frame(stats_frame)
    tags_frame.pack(fill="x", pady=Theme.scaled_val(5))
    tb.Label(
        tags_frame,
        text="ROLES & SYNERGIES",
        font=Theme.scaled_font(10, "bold"),
        bootstyle="primary",
    ).pack(anchor="w")

    if tags:
        tag_str = []
        for tag_key, count in sorted(
            tags.items(), key=lambda item: item[1], reverse=True
        ):
            ui_name = constants.TAG_VISUALS.get(tag_key, tag_key.capitalize())
            tag_str.append(f"{ui_name}: {count}")

        for i in range(0, len(tag_str), 4):
            ttk.Label(tags_frame, text="    ".join(tag_str[i : i + 4])).pack(
                anchor="w", pady=Theme.scaled_val(2)
            )
    else:
        tb.Label(
            tags_frame,
            text="No Scryfall tags found for this set.",
            bootstyle="secondary",
        ).pack(anchor="w", pady=Theme.scaled_val(2))

    if top_tribes:
        tribe_str = "  |  ".join([f"{t}: {c}" for t, c in top_tribes])
        ttk.Label(
            tags_frame, text=f"Top Tribes: {tribe_str}", font=Theme.scaled_font(9)
        ).pack(anchor="w", pady=Theme.scaled_val(4))

    ttk.Separator(stats_frame, orient="horizontal").pack(
        fill="x", pady=Theme.scaled_val(8)
    )

    curve_frame = ttk.Frame(stats_frame)
    curve_frame.pack(fill="x", pady=Theme.scaled_val(5))
    tb.Label(
        curve_frame,
        text=f"MANA CURVE (Avg CMC: {avg_cmc:.2f})",
        font=Theme.scaled_font(10, "bold"),
        bootstyle="primary",
    ).pack(anchor="w")
    for i in range(1, 7):
        lbl = f"{i} CMC: " if i < 6 else "6+ CMC:"
        cnt = curve[i]
        ttk.Label(
            curve_frame,
            text=f"{lbl:<8} {'█' * cnt} ({cnt})",
            font=Theme.scaled_font(10, family=constants.FONT_MONO_SPACE),
        ).pack(anchor="w", pady=Theme.scaled_val(1))


def clear_sample_hand(
    hand_container: ttk.Frame,
    hand_images: Optional[List[Any]] = None,
    hand_frames: Optional[List[Any]] = None,
) -> None:
    """Cleans up rendered sample hand widgets and references."""
    if hand_container and hand_container.winfo_exists():
        for widget in hand_container.winfo_children():
            widget.destroy()

    if hand_images is not None:
        hand_images.clear()
    if hand_frames is not None:
        hand_frames.clear()


def draw_sample_hand(
    hand_container: ttk.Frame,
    deck_list: List[Dict[str, Any]],
    hand_images: List[Any],
    hand_frames: List[Any],
    configuration: Any,
    after_fn: Callable,
    scale: Optional[float] = None,
) -> None:
    """Draws and displays a sample 7-card opening hand using CardImageLoader."""
    clear_sample_hand(hand_container, hand_images, hand_frames)

    if not hand_container or not hand_container.winfo_exists():
        return

    if not deck_list:
        ttk.Label(
            hand_container,
            text="Generate a deck first.",
            font=Theme.scaled_font(11),
        ).pack(pady=Theme.scaled_val(20))
        return

    flat_deck = []
    for c in deck_list:
        flat_deck.extend([c] * int(c.get("count", 1)))

    if len(flat_deck) < 7:
        ttk.Label(
            hand_container,
            text="Deck has fewer than 7 cards.",
            font=Theme.scaled_font(11),
        ).pack(pady=Theme.scaled_val(20))
        return

    hand = random.sample(flat_deck, 7)

    def hand_sort_key(c):
        types = c.get("types", [])
        name = c.get("name", "")
        cmc = int(c.get("cmc", 0))
        is_land = "Land" in types
        is_basic = "Basic" in types or name in constants.BASIC_LANDS

        if is_land:
            if is_basic:
                color_order = 5
                if "Plains" in name:
                    color_order = 0
                elif "Island" in name:
                    color_order = 1
                elif "Swamp" in name:
                    color_order = 2
                elif "Mountain" in name:
                    color_order = 3
                elif "Forest" in name:
                    color_order = 4
                return (0, color_order, name)
            return (1, 0, name)
        return (2, cmc, name)

    hand.sort(key=hand_sort_key)

    img_w = Theme.scaled_val(180)
    img_h = Theme.scaled_val(252)
    offset_y = Theme.scaled_val(32)

    stack_h = img_h + (6 * offset_y) + 20

    stack_container = ttk.Frame(hand_container, width=img_w, height=stack_h)
    stack_container.pack(expand=True, pady=Theme.scaled_val(15))
    stack_container.pack_propagate(False)

    def restore_z_order(event=None):
        for f in hand_frames:
            if f.winfo_exists():
                f.lift()

    for i, card in enumerate(hand):
        frame = tb.Frame(
            stack_container, width=img_w, height=img_h, bootstyle="secondary"
        )
        frame.pack_propagate(False)
        frame.place(x=0, y=i * offset_y)

        hand_frames.append(frame)

        name_lbl = tb.Label(
            frame,
            text=card.get("name", "Unknown"),
            font=Theme.scaled_font(9),
            wraplength=img_w - Theme.scaled_val(10),
            justify="center",
            bootstyle="inverse-secondary",
        )
        name_lbl.pack(expand=True)

        name_lbl.bind("<Enter>", lambda e, f=frame: f.lift())
        name_lbl.bind("<Leave>", restore_z_order)

        img_url = CardImageLoader.resolve_card_url(card)
        if not img_url:
            continue

        def make_callback(container_frame, card_ref):
            def on_loaded(im):
                if im is None:
                    return

                def apply_img():
                    if hasattr(container_frame, "winfo_exists") and container_frame.winfo_exists():
                        for w in container_frame.winfo_children():
                            w.destroy()

                        tk_img = ImageTk.PhotoImage(im)
                        hand_images.append(tk_img)
                        lbl = ttk.Label(container_frame, image=tk_img, cursor="hand2")
                        lbl.pack(fill="both", expand=True)

                        ui_scale = constants.UI_SIZE_DICT.get(
                            configuration.settings.ui_size, 1.0
                        )
                        lbl.bind(
                            "<Button-1>",
                            lambda e, c=card_ref: CardToolTip.create(
                                container_frame,
                                c,
                                configuration.features.images_enabled,
                                ui_scale,
                            ),
                        )
                        lbl.bind("<Enter>", lambda e: container_frame.lift())
                        lbl.bind("<Leave>", restore_z_order)

                try:
                    after_fn(0, apply_img)
                except Exception:
                    pass

            return on_loaded

        CardImageLoader.load_image_async(
            img_url, (img_w, img_h), make_callback(frame, card)
        )
