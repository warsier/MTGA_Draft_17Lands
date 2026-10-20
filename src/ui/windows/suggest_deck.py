"""
src/ui/windows/suggest_deck.py
Professional Deck Builder Panel.
Uses the Advisor Engine to suggest optimal archetypes from the pool.
Displays Main Deck and Sideboard in separate notebook tabs.
Includes a 10,000 game Monte Carlo Simulation for elite pro-level analysis.
"""

import tkinter
from tkinter import ttk
import ttkbootstrap as tb
from typing import Dict, Any, List
import random
import requests
import urllib.parse
import hashlib
import os
import io
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageTk

from src import constants
from src.card_logic import copy_deck, get_strict_colors, is_castable, get_functional_cmc
from src.ui.styles import Theme
from src.ui.components import DynamicTreeviewManager, CardToolTip, AutoScrollbar, CardImageLoader
from src.ui.deck_views import (
    show_sim_loading,
    show_sim_error,
    show_sim_results,
    render_deck_stats,
    clear_sample_hand,
    draw_sample_hand,
)
from src.utils import bind_scroll, normalize_color_string


class SuggestDeckPanel(ttk.Frame):
    def __init__(
        self,
        parent,
        draft_manager,
        configuration,
        on_export_custom=None,
        app_context=None,
    ):
        super().__init__(parent)
        self.draft = draft_manager
        self.configuration = configuration
        self.on_export_custom = on_export_custom
        self.app_context = app_context

        self.suggestions: Dict[str, Any] = {}
        self.current_deck_list: List[Dict] = []
        self.current_sb_list: List[Dict] = []
        self.current_archetype_key: str = ""

        self.is_building = False

        self.image_executor = CardImageLoader._image_executor
        self.sim_executor = ThreadPoolExecutor(max_workers=1)
        self.hand_images = []
        self.hand_frames = []

        self._build_ui()

    @property
    def table(self) -> ttk.Treeview:
        """Dynamically retrieves the current Main Deck tree widget from the manager."""
        return self.table_manager.tree if hasattr(self, "table_manager") else None

    @property
    def sb_table(self) -> ttk.Treeview:
        """Dynamically retrieves the current Sideboard tree widget from the manager."""
        return self.sb_manager.tree if hasattr(self, "sb_manager") else None

    def refresh(self):
        """Triggers the archetype building algorithm and refreshes the view."""
        self._calculate_suggestions()

    def _build_ui(self):
        self.header = ttk.Frame(self, style="Card.TFrame", padding=Theme.scaled_val(5))
        self.header.pack(fill="x", pady=Theme.scaled_val((0, 0)))

        # --- Row 1: Archetype Controls ---
        self.arch_frame = ttk.Frame(self.header, style="Card.TFrame")
        self.arch_frame.pack(fill="x")

        self.lbl_archetype = tb.Label(
            self.arch_frame,
            text="AI SUGGESTION:",
            font=Theme.scaled_font(8, "bold"),
            bootstyle="primary",
        )
        self.lbl_archetype.pack(side="left", padx=Theme.scaled_val(5))
        Theme.bind_theme_change(self, self._on_theme_change)

        self.var_archetype = tkinter.StringVar()
        self.om_archetype = ttk.OptionMenu(
            self.arch_frame,
            self.var_archetype,
            "",
            style="TMenubutton",
            command=self._on_deck_selection_change,
        )
        self.om_archetype.pack(
            side="left", padx=Theme.scaled_val(10), fill="x", expand=True
        )

        self.btn_copy = tb.Button(
            self.arch_frame, text="Copy Deck", width=12, command=self._copy_to_clipboard
        )
        self.btn_copy.pack(side="right", padx=Theme.scaled_val(5))

        if self.on_export_custom:
            self.btn_export_builder = tb.Button(
                self.arch_frame,
                text="Custom Builder",
                bootstyle="info-outline",
                command=lambda: self.on_export_custom(
                    self.current_deck_list, self.current_sb_list
                ),
            )
            self.btn_export_builder.pack(side="right", padx=Theme.scaled_val(5))

        self.notes_frame = ttk.Frame(
            self, style="Card.TFrame", padding=Theme.scaled_val((10, 0, 5, 5))
        )
        self.notes_frame.pack(fill="x", pady=Theme.scaled_val((0, 5)))
        self.lbl_deck_notes = tb.Label(
            self.notes_frame,
            text="",
            font=Theme.scaled_font(9),
            bootstyle="info",
        )
        self.lbl_deck_notes.pack(side="left", padx=Theme.scaled_val(5))

        # Replaced PanedWindow with a Notebook to give the tables maximum vertical space
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        # Main Deck Tab
        self.deck_frame = ttk.Frame(self.notebook, padding=Theme.scaled_val(2))
        self.notebook.add(self.deck_frame, text=" MAIN DECK (0) ")

        cols = ["name", "count", "cmc", "types", "colors", "gihwr"]
        self.table_manager = DynamicTreeviewManager(
            self.deck_frame,
            view_id="deck_builder",
            configuration=self.configuration,
            on_update_callback=self.refresh,
            static_columns=cols,
        )
        self.table_manager.pack(fill="both", expand=True)

        # Sideboard Tab
        self.sb_frame = ttk.Frame(self.notebook, padding=Theme.scaled_val(2))
        self.notebook.add(self.sb_frame, text=" SIDEBOARD ")

        self.sb_manager = DynamicTreeviewManager(
            self.sb_frame,
            view_id="deck_builder_sb",
            configuration=self.configuration,
            on_update_callback=self.refresh,
            static_columns=cols,
        )
        self.sb_manager.pack(fill="both", expand=True)

        # Stats Tab
        self.stats_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.stats_tab, text=" STATS & ANALYSIS ")

        self.stats_tab.rowconfigure(0, weight=1)
        self.stats_tab.columnconfigure(0, weight=1)

        self.stats_canvas = tkinter.Canvas(
            self.stats_tab, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        self.stats_scrollbar = AutoScrollbar(
            self.stats_tab, orient="vertical", command=self.stats_canvas.yview
        )

        self.stats_canvas.grid(row=0, column=0, sticky="nsew")
        self.stats_scrollbar.grid(row=0, column=1, sticky="ns")
        self.stats_canvas.configure(yscrollcommand=self.stats_scrollbar.set)

        self.stats_frame = ttk.Frame(self.stats_canvas, padding=Theme.scaled_val(15))
        self.stats_canvas_window = self.stats_canvas.create_window(
            (0, 0), window=self.stats_frame, anchor="nw"
        )

        def _on_content_resize(event):
            self.stats_canvas.configure(scrollregion=self.stats_canvas.bbox("all"))

        def _on_canvas_resize(event):
            self.stats_canvas.itemconfig(self.stats_canvas_window, width=event.width)

        self.stats_frame.bind("<Configure>", _on_content_resize)
        self.stats_canvas.bind("<Configure>", _on_canvas_resize)

        bind_scroll(self.stats_canvas, self.stats_canvas.yview_scroll)
        bind_scroll(self.stats_frame, self.stats_canvas.yview_scroll)
        self.stats_frame.bind(
            "<Enter>",
            lambda e: bind_scroll(self.stats_frame, self.stats_canvas.yview_scroll),
        )

        # --- SIMULATION & SAMPLE HAND TAB ---
        self.hand_tab = ttk.Frame(self.notebook, padding=Theme.scaled_val(15))
        self.notebook.add(self.hand_tab, text=" SIMULATION & SAMPLE HAND ")

        # Two Columns: 0 = Hand Canvas, 1 = Monte Carlo
        self.hand_tab.columnconfigure(0, weight=3)
        self.hand_tab.columnconfigure(1, weight=5)
        self.hand_tab.rowconfigure(1, weight=1)

        hand_control_bar = ttk.Frame(self.hand_tab)
        hand_control_bar.grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=Theme.scaled_val((0, 15))
        )

        self.btn_draw = tb.Button(
            hand_control_bar,
            text="Draw New Hand",
            command=self._draw_sample_hand,
            bootstyle="success-outline",
            width=16,
        )
        self.btn_draw.pack(side="left", padx=Theme.scaled_val(5))

        # Left Column: Scrollable Canvas for Sample Hand
        self.hand_canvas_frame = ttk.Frame(self.hand_tab)
        self.hand_canvas_frame.grid(
            row=1, column=0, sticky="nsew", padx=Theme.scaled_val((0, 15))
        )
        self.hand_canvas_frame.rowconfigure(0, weight=1)
        self.hand_canvas_frame.columnconfigure(0, weight=1)

        self.hand_canvas = tkinter.Canvas(
            self.hand_canvas_frame, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        self.hand_scrollbar = AutoScrollbar(
            self.hand_canvas_frame, orient="vertical", command=self.hand_canvas.yview
        )
        self.hand_canvas.grid(row=0, column=0, sticky="nsew")
        self.hand_scrollbar.grid(row=0, column=1, sticky="ns")
        self.hand_canvas.configure(yscrollcommand=self.hand_scrollbar.set)

        self.hand_container = ttk.Frame(self.hand_canvas)
        self.hand_canvas_window = self.hand_canvas.create_window(
            (0, 0), window=self.hand_container, anchor="n"
        )

        def _on_hand_content_resize(event):
            self.hand_canvas.configure(scrollregion=self.hand_canvas.bbox("all"))

        def _on_hand_canvas_resize(event):
            self.hand_canvas.itemconfig(self.hand_canvas_window, width=event.width)

        self.hand_container.bind("<Configure>", _on_hand_content_resize)
        self.hand_canvas.bind("<Configure>", _on_hand_canvas_resize)

        bind_scroll(self.hand_canvas, self.hand_canvas.yview_scroll)
        bind_scroll(self.hand_container, self.hand_canvas.yview_scroll)
        self.hand_container.bind(
            "<Enter>",
            lambda e: bind_scroll(self.hand_container, self.hand_canvas.yview_scroll),
        )

        # Right Column: Scrollable Monte Carlo Simulation
        self.sim_outer_frame = ttk.Labelframe(
            self.hand_tab,
            text=" MONTE CARLO SIMULATION (10,000 Games) ",
            padding=Theme.scaled_val(5),
        )
        self.sim_outer_frame.grid(row=1, column=1, sticky="nsew")

        self.sim_canvas = tkinter.Canvas(
            self.sim_outer_frame, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        self.sim_scrollbar = AutoScrollbar(
            self.sim_outer_frame, orient="vertical", command=self.sim_canvas.yview
        )
        self.sim_canvas.grid(row=0, column=0, sticky="nsew")
        self.sim_scrollbar.grid(row=0, column=1, sticky="ns")
        self.sim_outer_frame.rowconfigure(0, weight=1)
        self.sim_outer_frame.columnconfigure(0, weight=1)

        self.sim_canvas.configure(yscrollcommand=self.sim_scrollbar.set)

        self.sim_frame = ttk.Frame(self.sim_canvas, padding=Theme.scaled_val(15))
        self.sim_canvas_window = self.sim_canvas.create_window(
            (0, 0), window=self.sim_frame, anchor="nw"
        )

        def _on_sim_frame_resize(event):
            self.sim_canvas.configure(scrollregion=self.sim_canvas.bbox("all"))

        def _on_sim_canvas_resize(event):
            self.sim_canvas.itemconfig(self.sim_canvas_window, width=event.width)
            # Guarantee wrapped labels fit without clipping
            wrap_w = max(Theme.scaled_val(200), event.width - Theme.scaled_val(40))
            for child in self.sim_frame.winfo_children():
                if isinstance(child, ttk.Label) and getattr(
                    child, "is_dynamic_wrap", False
                ):
                    child.configure(wraplength=wrap_w)
            self.sim_canvas.configure(scrollregion=self.sim_canvas.bbox("all"))

        self.sim_frame.bind("<Configure>", _on_sim_frame_resize)
        self.sim_canvas.bind("<Configure>", _on_sim_canvas_resize)

        bind_scroll(self.sim_canvas, self.sim_canvas.yview_scroll)
        bind_scroll(self.sim_frame, self.sim_canvas.yview_scroll)
        self.sim_frame.bind(
            "<Enter>",
            lambda e: bind_scroll(self.sim_frame, self.sim_canvas.yview_scroll),
        )

        self.sim_label = ttk.Label(
            self.sim_frame,
            text="Generate a deck to analyze.",
            font=Theme.scaled_font(11),
        )
        self.sim_label.is_dynamic_wrap = True
        self.sim_label.pack(pady=Theme.scaled_val(20))

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _clear_table(self):
        """Highly defensive UI clearing to prevent attribute errors during state transitions."""
        table = getattr(self, "table", None)
        if table:
            for item in table.get_children():
                table.delete(item)

        sb_table = getattr(self, "sb_table", None)
        if sb_table:
            for item in sb_table.get_children():
                sb_table.delete(item)

        stats_frame = getattr(self, "stats_frame", None)
        if stats_frame and stats_frame.winfo_exists():
            for widget in stats_frame.winfo_children():
                widget.destroy()

        sim_frame = getattr(self, "sim_frame", None)
        if sim_frame and sim_frame.winfo_exists():
            for widget in sim_frame.winfo_children():
                widget.destroy()

        if hasattr(self, "_clear_sample_hand"):
            self._clear_sample_hand()

        self.current_deck_list = []
        self.current_sb_list = []

        notebook = getattr(self, "notebook", None)
        deck_frame = getattr(self, "deck_frame", None)
        if notebook and deck_frame:
            try:
                notebook.tab(deck_frame, text=" MAIN DECK (0) ")
            except Exception:
                pass

    def _on_tab_changed(self, event):
        current_tab = self.notebook.tab(self.notebook.select(), "text")
        if "SIMULATION & SAMPLE HAND" in current_tab:
            # Automatically draw a fresh hand when viewing the tab to ensure it's always responsive
            self._draw_sample_hand()

    def _run_monte_carlo_task(self, deck_list):
        self.after(0, lambda: self._show_sim_loading())
        try:
            from src.card_logic import simulate_deck

            stats = simulate_deck(deck_list, iterations=10000)
            self.after(0, lambda: self._show_sim_results(stats))
        except Exception as e:
            self.after(0, lambda e=e: self._show_sim_error(str(e)))

    def _show_sim_loading(self, msg="Running 10,000 Monte Carlo Simulations..."):
        show_sim_loading(getattr(self, "sim_frame", None), msg)

    def _show_sim_error(self, error):
        show_sim_error(getattr(self, "sim_frame", None), error)

    def _show_sim_results(self, stats, optimization_note=None):
        show_sim_results(
            getattr(self, "sim_frame", None),
            stats,
            self.current_deck_list,
            self.current_sb_list,
            optimization_note,
            getattr(self, "sim_canvas", None),
            self.after,
        )

    def _clear_sample_hand(self):
        clear_sample_hand(
            getattr(self, "hand_container", None),
            getattr(self, "hand_images", None),
            getattr(self, "hand_frames", None),
        )

    def _draw_sample_hand(self):
        draw_sample_hand(
            getattr(self, "hand_container", None),
            self.current_deck_list,
            self.hand_images,
            self.hand_frames,
            self.configuration,
            self.after,
        )

    def _on_theme_change(self, event=None):
        stats_canvas = getattr(self, "stats_canvas", None)
        if stats_canvas and stats_canvas.winfo_exists():
            stats_canvas.configure(bg=Theme.BG_PRIMARY)

        hand_canvas = getattr(self, "hand_canvas", None)
        if hand_canvas and hand_canvas.winfo_exists():
            hand_canvas.configure(bg=Theme.BG_PRIMARY)

    def _calculate_suggestions(self):
        raw_pool = self.draft.retrieve_taken_cards()

        playable_spells = [
            c for c in (raw_pool or []) if "Land" not in c.get("types", [])
        ]

        if not playable_spells or len(playable_spells) < 22:
            msg = (
                f"Not enough spells drafted yet (Have {len(playable_spells)}, Need 22)."
            )
            self._update_dropdown_options([msg])
            self.var_archetype.set(msg)
            if getattr(self, "app_context", None) and hasattr(
                self.app_context, "loading_overlay"
            ):
                self.app_context.loading_overlay.hide()
            return

        if self.is_building:
            return

        self.is_building = True
        self.var_archetype.set("Initializing AI Builder...")
        self._update_dropdown_options(["Initializing AI Builder..."])
        self._clear_table()
        self.suggestions = {}
        self.incremental_labels = []

        metrics = (
            self.orchestrator.scanner.retrieve_set_metrics()
            if hasattr(self, "orchestrator")
            else self.draft.retrieve_set_metrics()
        )
        _, event_type = (
            self.orchestrator.scanner.retrieve_current_limited_event()
            if hasattr(self, "orchestrator")
            else self.draft.retrieve_current_limited_event()
        )
        dataset_name = self.configuration.card_data.latest_dataset

        def _progress_cb(msg):
            if not self.winfo_exists():
                return
            if "status" in msg:
                if not self.suggestions:
                    self.after(0, lambda: self.var_archetype.set(msg["status"]))
                if getattr(self, "app_context", None) and hasattr(
                    self.app_context, "loading_overlay"
                ):
                    self.after(
                        0,
                        lambda: self.app_context.loading_overlay.update_status(
                            msg["status"]
                        ),
                    )
            elif "variant_label" in msg:
                lbl = msg["variant_label"]
                vd = msg["variant_data"]

                def _update_ui():
                    self.suggestions[lbl] = vd
                    self.incremental_labels.append(lbl)
                    self._update_dropdown_options(self.incremental_labels)

                    if len(self.incremental_labels) == 1:
                        self._on_deck_selection_change(lbl)

                self.after(0, _update_ui)

        def _worker():
            try:
                from src.card_logic import suggest_deck

                raw_results = suggest_deck(
                    raw_pool,
                    metrics,
                    self.configuration,
                    event_type,
                    _progress_cb,
                    dataset_name,
                )
                self.after(0, lambda: self._finalize_build(raw_results))
            except Exception as e:
                # Bind the message now: `e` is unbound once the except block
                # exits, before Tk runs the callback.
                self.after(0, lambda msg=str(e): self._handle_builder_error(msg))

        self.sim_executor.submit(_worker)

    def _finalize_build(self, sorted_decks):
        self.is_building = False
        if getattr(self, "app_context", None) and hasattr(
            self.app_context, "loading_overlay"
        ):
            self.app_context.loading_overlay.hide()

        if not sorted_decks:
            msg = "Not enough on-color playables to form a 40-card deck (Need 22)."
            self.suggestions = {}
            self._update_dropdown_options([msg])
            self.var_archetype.set(msg)
            self._clear_table()
            return

        self.suggestions = sorted_decks
        dropdown_labels = list(sorted_decks.keys())
        self._update_dropdown_options(dropdown_labels)

        # Always snap to the mathematically strongest deck once analysis completes
        self._on_deck_selection_change(dropdown_labels[0])

    def _handle_builder_error(self, error_msg):
        self.is_building = False
        if getattr(self, "app_context", None) and hasattr(
            self.app_context, "loading_overlay"
        ):
            self.app_context.loading_overlay.hide()

        msg = "Builder Error"
        self.suggestions = {}
        self._update_dropdown_options([msg])
        self.var_archetype.set(msg)
        self._clear_table()
        import logging

        logging.getLogger(__name__).error(f"Suggest Deck Error: {error_msg}")

    def _update_dropdown_options(self, options: List[str]):
        menu = self.om_archetype["menu"]
        menu.delete(0, "end")
        for opt in options:
            menu.add_command(
                label=opt, command=lambda v=opt: self._on_deck_selection_change(v)
            )

    def _on_deck_selection_change(self, label: str):
        if label in self.suggestions:
            self.var_archetype.set(label)
            self._render_deck(label)

    def _update_tables(self):
        """Helper to redraw tables after the Auto-Optimizer modifies the deck lists."""
        total_main_cards = sum(
            c.get(constants.DATA_FIELD_COUNT, 1) for c in self.current_deck_list
        )

        notebook = getattr(self, "notebook", None)
        deck_frame = getattr(self, "deck_frame", None)
        if notebook and deck_frame:
            try:
                notebook.tab(deck_frame, text=f" MAIN DECK ({total_main_cards}) ")
            except Exception:
                pass

        table = getattr(self, "table", None)
        if table:
            for item in table.get_children():
                table.delete(item)

        sb_table = getattr(self, "sb_table", None)
        if sb_table:
            for item in sb_table.get_children():
                sb_table.delete(item)

        from src.card_logic import row_color_tag

        def populate_tree(manager, source_list, is_sb):
            if not manager or not manager.tree:
                return
            tree = manager.tree

            if not getattr(tree, "_selection_bound", False):
                tree.bind(
                    "<ButtonRelease-1>", lambda e: self._on_selection(e, is_sb), add="+"
                )
                tree._selection_bound = True

            metrics = (
                self.orchestrator.scanner.retrieve_set_metrics()
                if hasattr(self, "orchestrator") and hasattr(self.orchestrator, "scanner")
                else (
                    self.draft.retrieve_set_metrics()
                    if hasattr(self.draft, "retrieve_set_metrics")
                    else getattr(self.draft, "set_metrics", None)
                )
            )
            raw_tier = (
                self.orchestrator.scanner.retrieve_tier_data()
                if hasattr(self, "orchestrator") and hasattr(self.orchestrator, "scanner")
                else (
                    self.draft.retrieve_tier_data()
                    if hasattr(self.draft, "retrieve_tier_data")
                    else getattr(self.draft, "tier_data", None)
                )
            )
            if isinstance(raw_tier, tuple):
                tier_data = raw_tier[0]
            elif isinstance(raw_tier, dict):
                tier_data = raw_tier
            else:
                tier_data = {}
            for idx, card in enumerate(source_list):
                row_values = DynamicTreeviewManager.format_row_values(
                    card,
                    manager.active_fields,
                    active_filter=self.current_archetype_key or "All Decks",
                    metrics=metrics,
                    result_format=self.configuration.settings.result_format,
                    tier_data=tier_data,
                )
                tag = DynamicTreeviewManager.get_row_tag(
                    card, idx, self.configuration.settings.card_colors_enabled
                )
                tree.insert(
                    "",
                    "end",
                    iid=str(idx),
                    text=card.get(constants.DATA_FIELD_NAME, "Unknown"),
                    values=row_values,
                    tags=(tag,),
                )
            if hasattr(tree, "reapply_sort"):
                tree.reapply_sort()

        if table:
            populate_tree(self.table_manager, self.current_deck_list, False)
        if sb_table:
            populate_tree(self.sb_manager, self.current_sb_list, True)

    def _render_deck_stats(self):
        render_deck_stats(
            getattr(self, "stats_frame", None),
            self.current_deck_list,
            show_subtypes=True,
        )

    def _render_deck(self, label: str):
        self._clear_table()
        data = self.suggestions.get(label)
        if not data:
            return

        deck_colors = data.get("colors", [])
        self.current_archetype_key = (
            normalize_color_string("".join(deck_colors)) if deck_colors else "All Decks"
        )
        if not self.current_archetype_key:
            self.current_archetype_key = "All Decks"

        self.current_deck_list = data.get("deck_cards", [])
        self.current_sb_list = data.get("sideboard_cards", [])

        def card_sort_key(x):
            return (
                x.get(constants.DATA_FIELD_CMC, 0),
                x.get(constants.DATA_FIELD_NAME, ""),
            )

        self.current_deck_list.sort(key=card_sort_key)
        self.current_sb_list.sort(key=card_sort_key)

        breakdown = data.get("breakdown", "")
        if hasattr(self, "lbl_deck_notes") and self.lbl_deck_notes.winfo_exists():
            self.lbl_deck_notes.config(text=breakdown)

        self._render_deck_stats()
        self._update_tables()

        stats = data.get("stats")
        opt_note = data.get("optimization_note")
        if stats:
            self._show_sim_results(stats, opt_note)
        else:
            self._show_sim_error("Simulation data missing.")

        notebook = getattr(self, "notebook", None)
        if notebook and notebook.winfo_exists():
            current_tab = notebook.tab(notebook.select(), "text")
            if "SIMULATION & SAMPLE HAND" in current_tab:
                self.after(100, self._draw_sample_hand)

    def _copy_to_clipboard(self):
        selection = self.var_archetype.get()
        if selection in self.suggestions:
            deck_data = self.suggestions[selection]
            export_text = copy_deck(
                deck_data.get("deck_cards", []), deck_data.get("sideboard_cards", [])
            )
            self.clipboard_clear()
            self.clipboard_append(export_text)

            self.btn_copy.config(text="Copied! ✔", bootstyle="success")
            self.after(
                2000,
                lambda: self.btn_copy.config(text="Copy Deck", bootstyle="primary"),
            )

    def _on_selection(self, event, is_sb=False):
        tree = getattr(self, "sb_table" if is_sb else "table", None)
        if not tree:
            return

        if hasattr(event, "x") and hasattr(event, "y"):
            region = tree.identify_region(event.x, event.y)
            if region not in ("tree", "cell"):
                return

        selection = tree.selection()
        if not selection:
            return

        item = tree.item(selection[0])
        card_name = item.get("text")

        source_list = self.current_sb_list if is_sb else self.current_deck_list

        if not card_name:
            idx = int(selection[0])
            if idx < len(source_list):
                card = source_list[idx]
            else:
                card = None
        else:
            card = next((c for c in source_list if c.get("name") == card_name), None)

        if card:
            CardToolTip.create(
                tree,
                card,
                self.configuration.features.images_enabled,
                constants.UI_SIZE_DICT.get(self.configuration.settings.ui_size, 1.0),
            )
