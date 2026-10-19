"""
src/ui/windows/custom_deck.py
Interactive Custom Deck Builder.
Allows users to manually construct decks, add basic lands, and run
Monte Carlo simulations on their custom creations.
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
import re
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageTk

from src import constants
from src.card_logic import (
    copy_deck,
    stack_cards,
    calculate_dynamic_mana_base,
    format_types_for_ui,
    get_strict_colors,
    is_castable,
    get_functional_cmc,
)
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


class CustomDeckPanel(ttk.Frame):
    def __init__(self, parent, draft_manager, configuration, app_context):
        super().__init__(parent)
        self.draft = draft_manager
        self.configuration = configuration
        self.app_context = app_context

        self.deck_list: List[Dict] = []
        self.sb_list: List[Dict] = []
        self.known_pool_size = 0

        self.image_executor = CardImageLoader._image_executor
        self.sim_executor = ThreadPoolExecutor(max_workers=1)
        self.hand_images = []
        self.hand_frames = []

        # Seed default columns in configuration if they don't exist
        if "custom_deck_main" not in self.configuration.settings.column_configs:
            self.configuration.settings.column_configs["custom_deck_main"] = [
                "name",
                "count",
                "cmc",
                "types",
                "colors",
                "gihwr",
            ]
        if "custom_deck_sb" not in self.configuration.settings.column_configs:
            self.configuration.settings.column_configs["custom_deck_sb"] = [
                "name",
                "count",
                "cmc",
                "types",
                "colors",
                "gihwr",
            ]

        self._build_ui()

    @property
    def main_table(self) -> ttk.Treeview:
        return self.deck_manager.tree if hasattr(self, "deck_manager") else None

    @property
    def sb_table(self) -> ttk.Treeview:
        return self.sb_manager.tree if hasattr(self, "sb_manager") else None

    def import_deck(self, deck_cards: List[Dict], sb_cards: List[Dict]):
        """Receives a deck from the Suggest Deck tab."""
        import copy

        self.deck_list = copy.deepcopy(deck_cards)
        self.sb_list = copy.deepcopy(sb_cards)

        raw_pool = self.draft.retrieve_taken_cards()
        self.known_pool_size = len(raw_pool) if raw_pool else 0

        self._update_tables()
        self._render_deck_stats()
        self._update_basics_toolbar()

        for widget in self.sim_frame.winfo_children():
            widget.destroy()
        self._clear_sample_hand()
        self.notebook.select(self.builder_tab)

    def refresh(self):
        """Appends newly drafted cards to the sideboard."""
        raw_pool = self.draft.retrieve_taken_cards()
        if not raw_pool:
            self.deck_list = []
            self.sb_list = []
            self.known_pool_size = 0
            self._update_tables()
            self._update_basics_toolbar()
            return

        if len(raw_pool) > self.known_pool_size:
            stacked_pool = stack_cards(raw_pool)

            for pool_card in stacked_pool:
                name = pool_card["name"]
                total_count = pool_card.get("count", 1)

                in_deck = next(
                    (c for c in self.deck_list if c["name"] == name), {}
                ).get("count", 0)
                in_sb = next((c for c in self.sb_list if c["name"] == name), {}).get(
                    "count", 0
                )

                diff = total_count - (in_deck + in_sb)
                if diff > 0:
                    sb_card = next((c for c in self.sb_list if c["name"] == name), None)
                    if sb_card:
                        sb_card["count"] += diff
                    else:
                        new_c = dict(pool_card)
                        new_c["count"] = diff
                        self.sb_list.append(new_c)

            self.known_pool_size = len(raw_pool)
            self._update_tables()
            self._render_deck_stats()

    def _build_ui(self):
        # --- CONTROL BAR ---
        self.header = ttk.Frame(self, style="Card.TFrame", padding=Theme.scaled_val(5))
        self.header.pack(fill="x", pady=Theme.scaled_val((0, 5)))

        tb.Button(
            self.header,
            text="Clear",
            bootstyle="danger-outline",
            command=self._clear_deck,
        ).pack(side="left", padx=Theme.scaled_val(5))

        tb.Button(
            self.header,
            text="Auto-Lands",
            bootstyle="info",
            command=self._apply_auto_lands,
        ).pack(side="left", padx=Theme.scaled_val(5))

        # Smart Basics Toolbar (Left Click = Add, Right Click = Remove)
        self.basics_frame = ttk.Frame(self.header, style="Card.TFrame")
        self.basics_frame.pack(side="left", padx=Theme.scaled_val((15, 2)))

        self.basic_buttons = {}
        basics = [
            ("W", "Plains", "light"),
            ("U", "Island", "info"),
            ("B", "Swamp", "dark"),
            ("R", "Mountain", "danger"),
            ("G", "Forest", "success"),
        ]
        for sym, name, style in basics:
            btn = tb.Button(
                self.basics_frame,
                text=f"{sym}: 0",
                bootstyle=style,
                width=5,
                padding=Theme.scaled_val(5),
            )

            # Standard Add (Left Click)
            btn.bind("<ButtonRelease-1>", lambda e, n=name: self._add_specific_basic(n))

            # Standard Remove (Right Click or Middle Click)
            btn.bind("<Button-2>", lambda e, n=name: self._on_basic_remove(e, n))
            btn.bind("<Button-3>", lambda e, n=name: self._on_basic_remove(e, n))

            # Mac Control-Click Remove
            btn.bind(
                "<Control-Button-1>", lambda e, n=name: self._on_basic_remove(e, n)
            )
            btn.bind("<Control-ButtonRelease-1>", lambda e: "break")

            btn.pack(side="left")
            self.basic_buttons[name] = btn

        self.btn_copy = tb.Button(
            self.header, text="Copy Deck", width=10, command=self._copy_to_clipboard
        )
        self.btn_copy.pack(side="right", padx=Theme.scaled_val(5))

        self.btn_sim = tb.Button(
            self.header,
            text="Analyze",
            bootstyle="success",
            command=self._run_simulation,
        )
        self.btn_sim.pack(side="right", padx=Theme.scaled_val(15))

        # --- TABBED LAYOUT ---
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        # 1. Builder Tab
        self.builder_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.builder_tab, text=" DECK BUILDER ")

        self.v_split = ttk.PanedWindow(self.builder_tab, orient=tkinter.VERTICAL)
        self.v_split.pack(
            fill="both", expand=True, padx=Theme.scaled_val(2), pady=Theme.scaled_val(2)
        )

        self.deck_frame = ttk.Labelframe(
            self.v_split, text=" MAIN DECK (0) ", padding=Theme.scaled_val(2)
        )
        self.v_split.add(self.deck_frame, weight=3)
        self.deck_manager = DynamicTreeviewManager(
            self.deck_frame,
            view_id="custom_deck_main",
            configuration=self.configuration,
            on_update_callback=self._update_tables,
        )
        self.deck_manager.pack(fill="both", expand=True)

        self.sb_frame = ttk.Labelframe(
            self.v_split, text=" SIDEBOARD (0) ", padding=Theme.scaled_val(2)
        )
        self.v_split.add(self.sb_frame, weight=2)
        self.sb_manager = DynamicTreeviewManager(
            self.sb_frame,
            view_id="custom_deck_sb",
            configuration=self.configuration,
            on_update_callback=self._update_tables,
        )
        self.sb_manager.pack(fill="both", expand=True)

        # 2. Stats Tab
        self.stats_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.stats_tab, text=" STATS & ANALYSIS ")
        self.stats_canvas = tkinter.Canvas(
            self.stats_tab, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        self.stats_scrollbar = AutoScrollbar(
            self.stats_tab, orient="vertical", command=self.stats_canvas.yview
        )
        self.stats_canvas.grid(row=0, column=0, sticky="nsew")
        self.stats_scrollbar.grid(row=0, column=1, sticky="ns")
        self.stats_tab.rowconfigure(0, weight=1)
        self.stats_tab.columnconfigure(0, weight=1)
        self.stats_canvas.configure(yscrollcommand=self.stats_scrollbar.set)
        self.stats_frame = ttk.Frame(self.stats_canvas, padding=Theme.scaled_val(15))
        self.stats_canvas_window = self.stats_canvas.create_window(
            (0, 0), window=self.stats_frame, anchor="nw"
        )

        self.stats_frame.bind(
            "<Configure>",
            lambda e: self.stats_canvas.configure(
                scrollregion=self.stats_canvas.bbox("all")
            ),
        )
        self.stats_canvas.bind(
            "<Configure>",
            lambda e: self.stats_canvas.itemconfig(
                self.stats_canvas_window, width=e.width
            ),
        )
        bind_scroll(self.stats_canvas, self.stats_canvas.yview_scroll)
        bind_scroll(self.stats_frame, self.stats_canvas.yview_scroll)

        # 3. Sim & Hand Tab
        self.hand_tab = ttk.Frame(self.notebook, padding=Theme.scaled_val(15))
        self.notebook.add(self.hand_tab, text=" SIMULATION & SAMPLE HAND ")
        self.hand_tab.columnconfigure(0, weight=3)
        self.hand_tab.columnconfigure(1, weight=5)
        self.hand_tab.rowconfigure(1, weight=1)

        hand_control_bar = ttk.Frame(self.hand_tab)
        hand_control_bar.grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=Theme.scaled_val((0, 15))
        )
        tb.Button(
            hand_control_bar,
            text="Draw New Hand",
            command=self._draw_sample_hand,
            bootstyle="success-outline",
            width=16,
        ).pack(side="left", padx=Theme.scaled_val(5))

        self.btn_optimize = tb.Button(
            hand_control_bar,
            text="🤖 Auto-Optimize Deck",
            command=self._auto_optimize_deck,
            bootstyle="info",
        )
        self.btn_optimize.pack(side="left", padx=Theme.scaled_val(10))

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
        self.hand_container.bind(
            "<Configure>",
            lambda e: self.hand_canvas.configure(
                scrollregion=self.hand_canvas.bbox("all")
            ),
        )
        self.hand_canvas.bind(
            "<Configure>",
            lambda e: self.hand_canvas.itemconfig(
                self.hand_canvas_window, width=e.width
            ),
        )
        bind_scroll(self.hand_canvas, self.hand_canvas.yview_scroll)
        bind_scroll(self.hand_container, self.hand_canvas.yview_scroll)

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

        # Attach the dynamic label wrapper to the canvas resize event
        def _on_sim_canvas_resize(event):
            self.sim_canvas.itemconfig(self.sim_canvas_window, width=event.width)
            wrap_w = max(Theme.scaled_val(200), event.width - Theme.scaled_val(40))
            for child in self.sim_frame.winfo_children():
                if getattr(child, "is_dynamic_wrap", False):
                    child.configure(wraplength=wrap_w)
            self.sim_canvas.configure(scrollregion=self.sim_canvas.bbox("all"))

        self.sim_frame.bind(
            "<Configure>",
            lambda e: self.sim_canvas.configure(
                scrollregion=self.sim_canvas.bbox("all")
            ),
        )
        self.sim_canvas.bind("<Configure>", _on_sim_canvas_resize)

        bind_scroll(self.sim_canvas, self.sim_canvas.yview_scroll)
        bind_scroll(self.sim_frame, self.sim_canvas.yview_scroll)

        self.sim_label = ttk.Label(
            self.sim_frame,
            text="Generate a deck to run simulations.",
            font=Theme.scaled_font(11),
        )
        self.sim_label.is_dynamic_wrap = True
        self.sim_label.pack(pady=Theme.scaled_val(20))

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _clear_table(self):
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
            self._draw_sample_hand()

    # --- SIMULATION AND OPTIMIZATION TASKS ---
    def _run_simulation(self):
        """Entry point for clicking the 'Analyze' button in the toolbar."""
        self.notebook.select(self.hand_tab)
        self.sim_executor.submit(self._run_monte_carlo_task, self.deck_list)

    def _run_monte_carlo_task(self, deck_list):
        """Executes a Monte Carlo simulation in the background."""
        self.after(
            0,
            lambda: self._show_sim_loading("Running 10,000 Monte Carlo Simulations..."),
        )
        try:
            from src.card_logic import simulate_deck

            stats = simulate_deck(deck_list, iterations=10000)
            self.after(0, lambda: self._show_sim_results(stats))
        except Exception as e:
            self.after(0, lambda err=str(e): self._show_sim_error(err))

    def _auto_optimize_deck(self):
        """Entry point for the AI Auto-Optimize button."""
        self.sim_executor.submit(self._run_auto_optimize_task)

    def _run_auto_optimize_task(self):
        """Background task that brute-forces deck permutations to find the mathematically optimal build."""
        self.after(
            0,
            lambda: self._show_sim_loading(
                "AI Auto-Optimizing: Simulating thousands of deck permutations..."
            ),
        )
        try:
            base_deck = list(self.deck_list)
            base_sb = list(self.sb_list)

            total_cards = sum(c.get("count", 1) for c in base_deck)
            if total_cards != 40:
                raise Exception(
                    f"Base deck must be exactly 40 cards to optimize (currently {total_cards})."
                )

            from src.card_logic import optimize_deck, get_strict_colors

            spells = [c for c in base_deck if "Land" not in c.get("types", [])]
            deck_colors = get_strict_colors(spells)
            archetype_key = (
                normalize_color_string("".join(deck_colors[:2]))
                if deck_colors
                else "All Decks"
            )

            final_deck, final_sb, final_stats, opt_note = optimize_deck(
                base_deck, base_sb, archetype_key, deck_colors
            )

            if final_deck:

                def finalize():
                    self.deck_list = final_deck
                    self.sb_list = final_sb

                    def card_sort_key(x):
                        return (
                            x.get(constants.DATA_FIELD_CMC, 0),
                            x.get(constants.DATA_FIELD_NAME, ""),
                        )

                    self.deck_list.sort(key=card_sort_key)
                    self.sb_list.sort(key=card_sort_key)

                    self._update_tables()
                    self._show_sim_results(final_stats, optimization_note=opt_note)
                    self._render_deck_stats()
                    self._draw_sample_hand()
                    self._update_basics_toolbar()

                self.after(0, finalize)
            else:
                raise Exception("Failed to optimize.")
        except Exception as e:
            # Bind the message now: `e` is unbound once the except block exits,
            # before Tk runs the callback.
            err_msg = str(e)

            def show_err():
                self._show_sim_error(err_msg)
                import tkinter.messagebox

                tkinter.messagebox.showwarning(
                    "Optimization Failed", err_msg, parent=self
                )

            self.after(0, show_err)

    def _show_sim_loading(self, msg="Running 10,000 Monte Carlo Simulations..."):
        show_sim_loading(getattr(self, "sim_frame", None), msg)

    def _show_sim_error(self, error):
        show_sim_error(getattr(self, "sim_frame", None), error)

    def _show_sim_results(self, stats, optimization_note=None):
        show_sim_results(
            getattr(self, "sim_frame", None),
            stats,
            self.deck_list,
            self.sb_list,
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
            self.deck_list,
            self.hand_images,
            self.hand_frames,
            self.configuration,
            self.after,
        )

    # --- BASIC LAND HANDLERS ---
    def _on_basic_remove(self, event, color_name):
        self._remove_specific_basic(color_name)
        return "break"

    # --- DRAG AND DROP & SELECTION LOGIC ---
    def _get_card_from_row(self, tree, row_id, is_sb):
        item = tree.item(row_id)
        card_name = item.get("text")

        if not card_name:
            manager = self.sb_manager if is_sb else self.deck_manager
            item_vals = item["values"]
            if not item_vals:
                return None
            try:
                name_idx = manager.active_fields.index("name")
                card_name = str(item_vals[name_idx])
            except ValueError:
                return None

        source_list = self.sb_list if is_sb else self.deck_list
        for c in source_list:
            if c["name"] == card_name:
                return c
        return None

    def _bind_dnd(self, tree, is_sb=False):
        if getattr(tree, "_dnd_bound", False):
            return
        tree._dnd_bound = True

        tree.bind(
            "<ButtonPress-1>", lambda e: self._on_drag_start(e, tree, is_sb), add="+"
        )
        tree.bind("<B1-Motion>", lambda e: self._on_drag_motion(e, tree), add="+")
        tree.bind(
            "<ButtonRelease-1>",
            lambda e: self._on_drag_release(e, tree, is_sb),
            add="+",
        )
        tree.bind(
            "<Double-Button-1>",
            lambda e: self._on_double_click(e, tree, is_sb),
            add="+",
        )
        tree.bind("<Button-3>", lambda e: self._on_right_click(e, tree, is_sb), add="+")
        tree.bind(
            "<Control-Button-1>",
            lambda e: self._on_right_click(e, tree, is_sb),
            add="+",
        )

    def _on_drag_start(self, event, tree, is_sb):
        self._drag_data = None
        row_id = tree.identify_row(event.y)
        if not row_id:
            return

        tree.selection_set(row_id)
        card = self._get_card_from_row(tree, row_id, is_sb)
        if not card:
            return

        self._drag_data = {
            "name": card["name"],
            "x": event.x_root,
            "y": event.y_root,
            "is_sb": is_sb,
        }

    def _on_drag_motion(self, event, tree):
        if getattr(self, "_drag_data", None):
            tree.configure(cursor="hand2")

    def _inside_widget(self, event, widget):
        rx, ry = widget.winfo_rootx(), widget.winfo_rooty()
        rw, rh = widget.winfo_width(), widget.winfo_height()
        return rx <= event.x_root <= rx + rw and ry <= event.y_root <= ry + rh

    def _on_drag_release(self, event, tree, is_sb):
        tree.configure(cursor="")
        if not getattr(self, "_drag_data", None):
            return

        dx, dy = (
            abs(event.x_root - self._drag_data["x"]),
            abs(event.y_root - self._drag_data["y"]),
        )
        card_name = self._drag_data["name"]

        if dx >= 5 or dy >= 5:
            if is_sb and self._inside_widget(event, self.deck_frame):
                self._move_card(self.sb_list, self.deck_list, card_name)
            elif not is_sb and self._inside_widget(event, self.sb_frame):
                self._move_card(self.deck_list, self.sb_list, card_name)

        self._drag_data = None

    def _on_double_click(self, event, tree, is_sb):
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        card = self._get_card_from_row(tree, row_id, is_sb)
        if not card:
            return

        if is_sb:
            self._move_card(self.sb_list, self.deck_list, card["name"])
        else:
            self._move_card(self.deck_list, self.sb_list, card["name"])
        return "break"

    def _on_right_click(self, event, tree, is_sb):
        if tree.identify_region(event.x, event.y) == "heading":
            return
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        tree.selection_set(row_id)

        card = self._get_card_from_row(tree, row_id, is_sb)
        if card:
            CardToolTip.create(
                tree,
                card,
                self.configuration.features.images_enabled,
                constants.UI_SIZE_DICT.get(self.configuration.settings.ui_size, 1.0),
            )

    # --- MOVE LOGIC ---
    def _move_card(self, source_list, dest_list, card_name):
        src_card = next((c for c in source_list if c["name"] == card_name), None)
        if not src_card:
            return

        src_card["count"] -= 1
        if src_card["count"] <= 0:
            source_list.remove(src_card)

        dest_card = next((c for c in dest_list if c["name"] == card_name), None)
        if dest_card:
            dest_card["count"] += 1
        else:
            new_c = dict(src_card)
            new_c["count"] = 1
            dest_list.append(new_c)

        self._update_tables()
        self._render_deck_stats()
        self._update_basics_toolbar()

    def _clear_deck(self):
        for card in list(self.deck_list):
            if card["name"] in constants.BASIC_LANDS:
                self.deck_list.remove(card)
            else:
                count = card["count"]
                sb_card = next(
                    (c for c in self.sb_list if c["name"] == card["name"]), None
                )
                if sb_card:
                    sb_card["count"] += count
                else:
                    new_c = dict(card)
                    self.sb_list.append(new_c)
                self.deck_list.remove(card)
        self._update_tables()
        self._render_deck_stats()
        self._update_basics_toolbar()

    # --- MANA BASE LOGIC ---
    def _apply_auto_lands(self):
        """Dispatches the brute-force mana optimization to a background thread."""
        self.notebook.select(self.hand_tab)
        self.sim_executor.submit(self._run_auto_lands_task)

    def _run_auto_lands_task(self):
        self.after(
            0,
            lambda: self._show_sim_loading(
                "Simulating perfect mana base permutations..."
            ),
        )

        try:
            from src.advisor.mana_base import brute_force_mana_base, get_strict_colors

            # Extract non-basics
            spells = [c for c in self.deck_list if "Land" not in c.get("types", [])]
            non_basic_lands = [
                c
                for c in self.deck_list
                if "Land" in c.get("types", [])
                and "Basic" not in c.get("types", [])
                and c.get("name") not in constants.BASIC_LANDS
            ]

            if not spells:
                self.after(
                    0, lambda: self._show_sim_error("Add spells to the deck first.")
                )
                return

            deck_colors = get_strict_colors(spells)
            if not deck_colors:
                deck_colors = ["W", "U", "B", "R", "G"]

            total_lands_needed = max(0, 40 - sum(c.get("count", 1) for c in spells))

            # Trim excess non-basics if needed
            if sum(c.get("count", 1) for c in non_basic_lands) > total_lands_needed:
                non_basic_lands.sort(
                    key=lambda x: float(
                        x.get("deck_colors", {}).get("All Decks", {}).get("gihwr", 0.0)
                    ),
                    reverse=True,
                )
                trimmed_lands = []
                remaining = total_lands_needed
                for card in non_basic_lands:
                    count = min(card.get("count", 1), remaining)
                    if count > 0:
                        trimmed_lands.append(dict(card, count=count))
                        remaining -= count
                non_basic_lands = trimmed_lands

            needed_basics = max(
                0, total_lands_needed - sum(c.get("count", 1) for c in non_basic_lands)
            )

            # Trigger the AI brute force
            basics_to_add = brute_force_mana_base(
                spells, non_basic_lands, deck_colors, forced_count=needed_basics
            )

            def _finalize():
                # Wipe old basics
                self.deck_list = spells + non_basic_lands

                # Append the optimal ones
                for basic in basics_to_add:
                    dest_card = next(
                        (c for c in self.deck_list if c["name"] == basic["name"]), None
                    )
                    if dest_card:
                        dest_card["count"] += 1
                    else:
                        self.deck_list.append(dict(basic))

                self._update_tables()
                self._render_deck_stats()
                self._update_basics_toolbar()
                self._run_monte_carlo_task(self.deck_list)

            self.after(0, _finalize)

        except Exception as e:
            self.after(0, lambda err=str(e): self._show_sim_error(err))

    def _add_specific_basic(self, color_name):
        color_map = {
            "Plains": "W",
            "Island": "U",
            "Swamp": "B",
            "Mountain": "R",
            "Forest": "G",
        }
        color = color_map.get(color_name, "")

        dest_card = next((c for c in self.deck_list if c["name"] == color_name), None)
        if dest_card:
            dest_card["count"] += 1
        else:
            card = {
                "name": color_name,
                "cmc": 0,
                "types": ["Land", "Basic"],
                "colors": [color] if color else [],
                "count": 1,
            }
            self.deck_list.append(card)

        self._update_tables()
        self._render_deck_stats()
        self._update_basics_toolbar()

    def _remove_specific_basic(self, color_name):
        dest_card = next((c for c in self.deck_list if c["name"] == color_name), None)
        if not dest_card:
            return

        dest_card["count"] -= 1
        if dest_card["count"] <= 0:
            self.deck_list.remove(dest_card)

        self._update_tables()
        self._render_deck_stats()
        self._update_basics_toolbar()

    def _update_basics_toolbar(self):
        """Updates the numeric counts on the basic land buttons."""
        counts = {
            name: 0
            for _, name, _ in [
                ("W", "Plains", ""),
                ("U", "Island", ""),
                ("B", "Swamp", ""),
                ("R", "Mountain", ""),
                ("G", "Forest", ""),
            ]
        }

        for c in self.deck_list:
            if c["name"] in counts:
                counts[c["name"]] += c.get("count", 1)

        symbol_map = {
            "Plains": "W",
            "Island": "U",
            "Swamp": "B",
            "Mountain": "R",
            "Forest": "G",
        }

        for name, btn in self.basic_buttons.items():
            count = counts[name]
            sym = symbol_map[name]
            btn.configure(text=f"{sym}: {count}")

    # --- RENDERING ---
    def _update_tables(self):
        total_main = sum(c.get("count", 1) for c in self.deck_list)
        total_sb = sum(c.get("count", 1) for c in self.sb_list)

        # Smart Deck Size Warning
        if total_main == 40:
            self.deck_frame.configure(text=f" MAIN DECK ({total_main}) ✔ ")
        elif total_main > 40:
            self.deck_frame.configure(
                text=f" MAIN DECK ({total_main}) ⚠️ CUT {total_main - 40} "
            )
        else:
            self.deck_frame.configure(
                text=f" MAIN DECK ({total_main}) ⚠️ ADD {40 - total_main} "
            )

        self.sb_frame.configure(text=f" SIDEBOARD ({total_sb}) ")

        metrics = self.draft.retrieve_set_metrics()
        tier_data = self.draft.retrieve_tier_data()
        active_filter = self.configuration.settings.deck_filter

        if active_filter == constants.FILTER_OPTION_AUTO:
            active_filter = "All Decks"

        from src.card_logic import format_win_rate, row_color_tag

        def populate_tree(manager, source_list, is_sb):
            tree = manager.tree
            self._bind_dnd(tree, is_sb)

            for item in tree.get_children():
                tree.delete(item)

            sorted_source = sorted(
                source_list,
                key=lambda x: (
                    x.get(constants.DATA_FIELD_CMC, 0),
                    x.get(constants.DATA_FIELD_NAME, ""),
                ),
            )

            for idx, card in enumerate(sorted_source):
                row_values = DynamicTreeviewManager.format_row_values(
                    card,
                    manager.active_fields,
                    active_filter=active_filter,
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
                    text=card.get("name", ""),
                    values=row_values,
                    tags=(tag,),
                )

            if hasattr(tree, "reapply_sort"):
                tree.reapply_sort()

        populate_tree(self.deck_manager, self.deck_list, False)
        populate_tree(self.sb_manager, self.sb_list, True)

    def _render_deck_stats(self):
        render_deck_stats(
            getattr(self, "stats_frame", None),
            self.deck_list,
            show_subtypes=True,
        )

    def _copy_to_clipboard(self):
        self.clipboard_clear()
        self.clipboard_append(copy_deck(self.deck_list, self.sb_list))
        self.btn_copy.configure(text="Copied! ✔", bootstyle="success")
        self.after(
            2000, lambda: self.btn_copy.configure(text="Copy Deck", bootstyle="primary")
        )
