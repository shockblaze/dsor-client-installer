"""Tk front-end for the server's login/register service.

A login window in the style of the retail client's launcher. Registering,
logging in, and changing a password are requests to the account service; the
session it issues is what the game client is started with. Every network call
runs off the UI thread so a slow or unreachable server cannot freeze the
window.

The window has three states. It opens on a loader while the account service
is probed, then shows either the login form or, when the service cannot be
reached, "Servers are currently down" with a retry. Settings stay reachable
in every state so the address can be corrected.

    python dso_launcher_gui.py
    python dso_client.py gui
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from math import sqrt
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_client import (  # noqa: E402
    AccountServiceError,
    ServiceUnreachableError,
    normalize_server_url,
)
from dso_client import (  # noqa: E402
    LANGUAGES,
    ClientConfig,
    LauncherError,
    build_arguments,
    ensure_client_temp_directory,
    format_duration,
    resolve_client_path,
    start_client,
)


WINDOW_WIDTH = 420
WINDOW_HEIGHT = 560

BG_TOP = "#16232b"
BG_BOTTOM = "#070b0e"
PANEL = "#101a20"
FIELD_BG = "#d7d7d7"
FIELD_FG = "#1b1b1b"
PLACEHOLDER_FG = "#8d8d8d"
GOLD = "#e2a63a"
GOLD_DIM = "#a9761f"
LINK = "#e8c760"
TEXT = "#c9d3d8"
MUTED = "#7d8d96"
ERROR = "#e2685a"
OK = "#8fca4a"

GREEN_TOP = "#8ecb2c"
GREEN_BOTTOM = "#3f7a0c"
GREEN_EDGE = "#254f06"
BLUE_TOP = "#5a7fb8"
BLUE_BOTTOM = "#2c4a76"
BLUE_EDGE = "#1b3050"

# Which canvas tag is visible in each window state.
STATE_TAGS = {"checking": "waiting", "online": "form", "offline": "offline"}
# A server on the same machine answers in a millisecond. Holding the loader
# briefly keeps that from reading as a flicker on the way to the form.
MIN_LOADER_SECONDS = 0.45

TITLE_FONT = ("Georgia", 30, "bold")
SUB_FONT = ("Georgia", 13)
UI_FONT = ("Segoe UI", 10)
UI_BOLD = ("Segoe UI", 10, "bold")
SMALL_FONT = ("Segoe UI", 9)


def _blend(first: str, second: str, ratio: float) -> str:
    """Interpolate two ``#rrggbb`` colors."""

    ratio = max(0.0, min(1.0, ratio))
    channels = []
    for offset in (1, 3, 5):
        start = int(first[offset:offset + 2], 16)
        end = int(second[offset:offset + 2], 16)
        channels.append(int(round(start + (end - start) * ratio)))
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _lighten(color: str, amount: float = 0.18) -> str:
    return _blend(color, "#ffffff", amount)


def paint_gradient(
    canvas: tk.Canvas,
    width: int,
    height: int,
    top: str,
    bottom: str,
    *,
    tags: str = "background",
) -> None:
    for y in range(height):
        canvas.create_line(
            0,
            y,
            width,
            y,
            fill=_blend(top, bottom, y / max(1, height - 1)),
            tags=tags,
        )


class GradientButton(tk.Canvas):
    """A rounded, vertically shaded button with a hover and pressed state."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command,
        *,
        width: int = 300,
        height: int = 44,
        top: str = GREEN_TOP,
        bottom: str = GREEN_BOTTOM,
        edge: str = GREEN_EDGE,
        text_color: str = "#ffffff",
        font=("Segoe UI", 12, "bold"),
        background: str = PANEL,
        radius: int = 9,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=background,
        )
        self._command = command
        self._size = (width, height)
        self._colors = (top, bottom, edge)
        self._text = text
        self._text_color = text_color
        self._font = font
        self._radius = radius
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _row_inset(self, y: int, height: int) -> float:
        """Horizontal inset for row ``y`` so the shading looks rounded."""

        radius = self._radius
        distance = None
        if y < radius:
            distance = radius - y
        elif y > height - 1 - radius:
            distance = y - (height - 1 - radius)
        if distance is None:
            return 0.0
        return radius - sqrt(max(0.0, radius * radius - distance * distance))

    def _render(self) -> None:
        self.delete("all")
        width, height = self._size
        top, bottom, edge = self._colors
        if not self._enabled:
            top, bottom = _blend(top, "#5a5a5a", 0.6), _blend(bottom, "#3a3a3a", 0.6)
            edge = _blend(edge, "#3a3a3a", 0.5)
        elif self._pressed:
            top, bottom = bottom, top
        elif self._hover:
            top, bottom = _lighten(top, 0.16), _lighten(bottom, 0.16)

        for y in range(height):
            inset = self._row_inset(y, height)
            self.create_line(
                inset,
                y,
                width - inset,
                y,
                fill=_blend(top, bottom, y / max(1, height - 1)),
            )
        self.create_arc(
            0, 0, self._radius * 2, self._radius * 2,
            start=90, extent=90, style=tk.ARC, outline=edge,
        )
        self.create_arc(
            width - self._radius * 2 - 1, 0, width - 1, self._radius * 2,
            start=0, extent=90, style=tk.ARC, outline=edge,
        )
        self.create_arc(
            0, height - self._radius * 2 - 1, self._radius * 2, height - 1,
            start=180, extent=90, style=tk.ARC, outline=edge,
        )
        self.create_arc(
            width - self._radius * 2 - 1, height - self._radius * 2 - 1,
            width - 1, height - 1,
            start=270, extent=90, style=tk.ARC, outline=edge,
        )
        self.create_line(self._radius, 0, width - self._radius, 0, fill=edge)
        self.create_line(
            self._radius, height - 1, width - self._radius, height - 1, fill=edge
        )
        self.create_line(0, self._radius, 0, height - self._radius, fill=edge)
        self.create_line(
            width - 1, self._radius, width - 1, height - self._radius, fill=edge
        )
        self.create_line(
            self._radius + 1, 1, width - self._radius - 1, 1,
            fill=_lighten(top, 0.35),
        )
        offset = 1 if self._pressed else 0
        text_color = self._text_color if self._enabled else "#9aa0a2"
        self.create_text(
            width / 2 + 1,
            height / 2 + 1 + offset,
            text=self._text,
            fill="#1d2a10" if self._enabled else "#4a4a4a",
            font=self._font,
        )
        self.create_text(
            width / 2,
            height / 2 + offset,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def configure_text(self, text: str) -> None:
        self._text = text
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._hover = False
        self._pressed = False
        self._render()

    def _on_enter(self, _event) -> None:
        if self._enabled:
            self._hover = True
            self.configure(cursor="hand2")
            self._render()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._pressed = False
        self._render()

    def _on_press(self, _event) -> None:
        if self._enabled:
            self._pressed = True
            self._render()

    def _on_release(self, _event) -> None:
        if not self._enabled or not self._pressed:
            return
        self._pressed = False
        self._render()
        if self._command is not None:
            self._command()


class PlaceholderEntry(tk.Entry):
    """Entry with inline placeholder text, matching the retail login fields."""

    def __init__(
        self,
        master: tk.Misc,
        placeholder: str,
        *,
        secret: bool = False,
        font=("Segoe UI", 11),
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            font=font,
            bg=FIELD_BG,
            fg=FIELD_FG,
            insertbackground=FIELD_FG,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            **kwargs,
        )
        self._placeholder = placeholder
        self._secret = secret
        self._showing_placeholder = False
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.delete(0, tk.END)
        self.configure(fg=PLACEHOLDER_FG, show="")
        self.insert(0, self._placeholder)
        self._showing_placeholder = True

    def _on_focus_in(self, _event) -> None:
        if self._showing_placeholder:
            self.delete(0, tk.END)
            self.configure(fg=FIELD_FG, show="•" if self._secret else "")
            self._showing_placeholder = False

    def _on_focus_out(self, _event) -> None:
        if not super().get():
            self._show_placeholder()

    def get(self) -> str:  # type: ignore[override]
        return "" if self._showing_placeholder else super().get()

    def set(self, value: str) -> None:
        self.delete(0, tk.END)
        if value:
            self.configure(fg=FIELD_FG, show="•" if self._secret else "")
            self.insert(0, value)
            self._showing_placeholder = False
        else:
            self._show_placeholder()


class ToggleCheck(tk.Canvas):
    """A checkbox drawn to match the panel instead of the native widget."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        *,
        background: str = PANEL,
        font=SMALL_FONT,
        width: int = 240,
        height: int = 20,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=background,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._variable = variable
        self._text = text
        self._font = font
        self._render()
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_args: self._render())

    def _render(self) -> None:
        self.delete("all")
        checked = bool(self._variable.get())
        self.create_rectangle(
            1, 3, 15, 17,
            fill="#16232b" if not checked else "#1e3320",
            outline="#4b6070" if not checked else GREEN_TOP,
        )
        if checked:
            self.create_line(4, 10, 7, 14, fill=GREEN_TOP, width=2)
            self.create_line(7, 14, 13, 5, fill=GREEN_TOP, width=2)
        self.create_text(
            22, 10, anchor="w", text=self._text, fill=TEXT, font=self._font
        )

    def _toggle(self, _event) -> None:
        self._variable.set(not self._variable.get())


class Spinner(tk.Canvas):
    """A rotating arc shown while the launcher waits on the server."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        size: int = 46,
        background: str = PANEL,
        color: str = GOLD,
        track: str = "#22333c",
        thickness: int = 4,
        interval_ms: int = 45,
    ) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=background,
            highlightthickness=0,
            bd=0,
        )
        self._size = size
        self._color = color
        self._track = track
        self._thickness = thickness
        self._interval_ms = interval_ms
        self._angle = 90
        self._job = None
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        inset = self._thickness
        box = (inset, inset, self._size - inset, self._size - inset)
        self.create_oval(*box, outline=self._track, width=self._thickness)
        self.create_arc(
            *box,
            start=self._angle,
            extent=95,
            style=tk.ARC,
            outline=self._color,
            width=self._thickness,
        )

    def _tick(self) -> None:
        self._angle = (self._angle - 11) % 360
        self._draw()
        try:
            self._job = self.after(self._interval_ms, self._tick)
        except tk.TclError:
            self._job = None

    def start(self) -> None:
        if self._job is None:
            self._tick()

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None


class LinkLabel(tk.Label):
    """A flat clickable label which underlines on hover."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command,
        *,
        color: str = LINK,
        font=SMALL_FONT,
        background: str = PANEL,
    ) -> None:
        super().__init__(
            master,
            text=text,
            fg=color,
            bg=background,
            font=font,
            cursor="hand2",
            bd=0,
        )
        self._font = font
        self.bind("<Button-1>", lambda _event: command())
        self.bind("<Enter>", lambda _e: self.configure(font=(*font, "underline")))
        self.bind("<Leave>", lambda _e: self.configure(font=font))


def apply_dark_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Dark.Treeview",
        background="#101a20",
        fieldbackground="#101a20",
        foreground=TEXT,
        bordercolor="#24343d",
        rowheight=24,
        font=SMALL_FONT,
    )
    style.configure(
        "Dark.Treeview.Heading",
        background="#1a2831",
        foreground=GOLD,
        relief=tk.FLAT,
        font=("Segoe UI", 9, "bold"),
    )
    style.map(
        "Dark.Treeview",
        background=[("selected", "#2c4a2a")],
        foreground=[("selected", "#ffffff")],
    )
    style.map("Dark.Treeview.Heading", background=[("active", "#24343d")])
    style.configure(
        "Dark.TCombobox",
        fieldbackground=FIELD_BG,
        background=FIELD_BG,
        foreground=FIELD_FG,
        arrowcolor="#2b2b2b",
        bordercolor="#2b3b45",
    )
    return style


def describe_error(exc: BaseException) -> str:
    """One line a player can act on, whatever failed underneath.

    Never includes the server's address; ``AccountServiceError`` already
    keeps it out of its messages, and it stays out of the window.
    """

    if isinstance(exc, ServiceUnreachableError):
        return f"{exc}. Check the server address in Settings."
    if isinstance(exc, (AccountServiceError, LauncherError)):
        return str(exc)
    if isinstance(exc, OSError):
        return f"Could not start the client: {exc}"
    return f"{type(exc).__name__}: {exc}"


class ModalDialog(tk.Toplevel):
    """Shared chrome for the register/settings/password dialogs."""

    def __init__(self, parent: tk.Misc, title: str, width: int, height: int) -> None:
        super().__init__(parent)
        self.title(title)
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result = None
        parent_window = parent.winfo_toplevel()
        x = parent_window.winfo_rootx() + (parent_window.winfo_width() - width) // 2
        y = parent_window.winfo_rooty() + (parent_window.winfo_height() - height) // 3
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.body = tk.Frame(self, bg=PANEL, padx=18, pady=16)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.status = tk.Label(
            self, text="", bg=PANEL, fg=ERROR, font=SMALL_FONT, wraplength=width - 36
        )
        self.status.pack(fill=tk.X, padx=18, pady=(0, 10))
        self.bind("<Escape>", lambda _event: self.destroy())

    def field(self, row: int, label: str, **entry_options) -> tk.Entry:
        tk.Label(
            self.body, text=label, bg=PANEL, fg=TEXT, font=SMALL_FONT, anchor="w"
        ).grid(row=row, column=0, sticky="w", pady=(6, 2))
        entry = tk.Entry(
            self.body,
            font=UI_FONT,
            bg=FIELD_BG,
            fg=FIELD_FG,
            insertbackground=FIELD_FG,
            relief=tk.FLAT,
            bd=0,
            **entry_options,
        )
        entry.grid(row=row, column=1, sticky="ew", pady=(6, 2), ipady=4, padx=(10, 0))
        self.body.columnconfigure(1, weight=1)
        return entry

    def fail(self, message: str) -> None:
        self.status.configure(text=message, fg=ERROR)

    def working(self, message: str) -> None:
        self.status.configure(text=message, fg=MUTED)

    def run_async(self, work, on_success, *, busy: str = "Contacting server...") -> None:
        """Run a request off the UI thread and resume on it."""

        self.working(busy)
        for child in self.body.winfo_children():
            if isinstance(child, GradientButton):
                child.set_enabled(False)

        def finish() -> None:
            for child in self.body.winfo_children():
                if isinstance(child, GradientButton):
                    child.set_enabled(True)

        def succeeded(value) -> None:
            finish()
            on_success(value)

        def failed(exc: BaseException) -> None:
            # Surfaced in the dialog rather than crashing the worker.
            finish()
            self.fail(describe_error(exc))

        run_off_thread(self, work, succeeded, failed)

    def show(self):
        self.grab_set()
        self.wait_window()
        return self.result


class RegisterDialog(ModalDialog):
    """Creates an account on the server."""

    def __init__(self, parent: tk.Misc, config: ClientConfig) -> None:
        super().__init__(parent, "Register account", 430, 330)
        self.config_store = config
        tk.Label(
            self.body,
            text="Your account is created on the server you are connected to.",
            bg=PANEL,
            fg=MUTED,
            font=SMALL_FONT,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.username = self.field(1, "Username")
        self.password = self.field(2, "Password", show="•")
        self.confirm = self.field(3, "Repeat password", show="•")
        tk.Label(
            self.body,
            text=(
                "3-24 characters for the name; at least 6 for the password. "
                "The server assigns the account ID and a character."
            ),
            bg=PANEL,
            fg=MUTED,
            font=SMALL_FONT,
            wraplength=370,
            justify=tk.LEFT,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 4))

        buttons = tk.Frame(self.body, bg=PANEL)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.submit_button = GradientButton(
            buttons, "REGISTER", self._submit, width=170, height=38, font=UI_BOLD
        )
        self.submit_button.pack(side=tk.LEFT)
        GradientButton(
            buttons, "Cancel", self.destroy, width=110, height=38,
            top="#556069", bottom="#333c44", edge="#20272c", font=UI_BOLD,
        ).pack(side=tk.RIGHT)
        self.username.focus_set()
        self.bind("<Return>", lambda _event: self._submit())

    def _submit(self) -> None:
        username = self.username.get().strip()
        password = self.password.get()
        if not username:
            self.fail("Enter a username.")
            return
        if password != self.confirm.get():
            self.fail("Passwords do not match.")
            return
        client = self.config_store.client()

        def work():
            client.register(username, password)
            return client.login(username, password)

        def done(session) -> None:
            self.config_store.store_session(session)
            self.result = session
            self.destroy()

        self.submit_button.set_enabled(False)
        self.run_async(work, done, busy=f"Registering {username}...")


class PasswordDialog(ModalDialog):
    """Changes an account's password on the server."""

    def __init__(self, parent: tk.Misc, config: ClientConfig, username: str) -> None:
        super().__init__(parent, "Change password", 420, 280)
        self.config_store = config
        self.username_entry = self.field(0, "Username")
        self.username_entry.insert(0, username)
        self.current = self.field(1, "Current password", show="•")
        self.new_password = self.field(2, "New password", show="•")
        self.confirm = self.field(3, "Repeat new", show="•")
        tk.Label(
            self.body,
            text="Changing the password ends every session of that account.",
            bg=PANEL,
            fg=MUTED,
            font=SMALL_FONT,
            wraplength=360,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 4))
        buttons = tk.Frame(self.body, bg=PANEL)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        GradientButton(
            buttons, "SAVE", self._submit, width=150, height=36, font=UI_BOLD
        ).pack(side=tk.LEFT)
        GradientButton(
            buttons, "Cancel", self.destroy, width=110, height=36,
            top="#556069", bottom="#333c44", edge="#20272c", font=UI_BOLD,
        ).pack(side=tk.RIGHT)
        (self.current if username else self.username_entry).focus_set()
        self.bind("<Return>", lambda _event: self._submit())

    def _submit(self) -> None:
        username = self.username_entry.get().strip()
        if not username:
            self.fail("Enter the username.")
            return
        new_password = self.new_password.get()
        if new_password != self.confirm.get():
            self.fail("New passwords do not match.")
            return
        client = self.config_store.client()
        current = self.current.get()

        def done(_account) -> None:
            self.config_store.clear_session()
            self.result = username
            self.destroy()

        self.run_async(
            lambda: client.change_password(username, current, new_password),
            done,
            busy="Changing password...",
        )


class SettingsDialog(ModalDialog):
    """Client-side settings: which server, which executable, what overrides."""

    def __init__(self, parent: tk.Misc, config: ClientConfig) -> None:
        super().__init__(parent, "Settings", 470, 380)
        self.config_store = config
        self.server_url = self.field(0, "Account service")
        self.server_url.insert(0, str(config.setting("server_url")))
        self.client_path = self.field(1, "Client")
        self.client_path.insert(0, str(config.setting("client_path")))
        LinkLabel(self.body, "Browse...", self._browse, color=LINK).grid(
            row=2, column=1, sticky="e"
        )
        tk.Label(
            self.body,
            text="Leave the overrides empty to use what the server reports.",
            bg=PANEL,
            fg=MUTED,
            font=SMALL_FONT,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.host = self.field(4, "Game address (-ip, -bica)")
        self.host.insert(0, str(config.setting("host_override")))
        self.gateway_port = self.field(5, "Gateway port (-port)")
        gateway_override = int(config.setting("gateway_port_override") or 0)
        self.gateway_port.insert(0, str(gateway_override or ""))
        self.chat_port = self.field(6, "Chat port (-bicp)")
        port_override = int(config.setting("chat_port_override") or 0)
        self.chat_port.insert(0, str(port_override or ""))
        self.instance_id = self.field(7, "Instance (-instanceid)")
        instance_override = int(config.setting("instance_id_override") or 0)
        self.instance_id.insert(0, str(instance_override or ""))

        buttons = tk.Frame(self.body, bg=PANEL)
        buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        GradientButton(
            buttons, "SAVE", self._submit, width=150, height=36, font=UI_BOLD
        ).pack(side=tk.LEFT)
        GradientButton(
            buttons, "Test connection", self._test, width=140, height=36,
            top=BLUE_TOP, bottom=BLUE_BOTTOM, edge=BLUE_EDGE, font=UI_BOLD,
        ).pack(side=tk.LEFT, padx=8)
        GradientButton(
            buttons, "Cancel", self.destroy, width=100, height=36,
            top="#556069", bottom="#333c44", edge="#20272c", font=UI_BOLD,
        ).pack(side=tk.RIGHT)
        self.bind("<Return>", lambda _event: self._submit())

    def _browse(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Select the client executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.client_path.delete(0, tk.END)
            self.client_path.insert(0, selected)

    def _collect(self) -> dict | None:
        try:
            server_url = normalize_server_url(self.server_url.get())
        except AccountServiceError as exc:
            self.fail(str(exc))
            return None
        raw_gateway = self.gateway_port.get().strip()
        raw_port = self.chat_port.get().strip()
        raw_instance = self.instance_id.get().strip()
        try:
            gateway_port = int(raw_gateway) if raw_gateway else 0
            chat_port = int(raw_port) if raw_port else 0
            instance_id = int(raw_instance) if raw_instance else 0
        except ValueError:
            self.fail("Ports and instance must be numbers, or empty.")
            return None
        if gateway_port and not 0 < gateway_port <= 0xFFFF:
            self.fail("Gateway port must be a valid UDP port.")
            return None
        if chat_port and not 0 < chat_port <= 0xFFFF:
            self.fail("Chat port must be a valid UDP port.")
            return None
        if instance_id < 0:
            self.fail("Instance ID cannot be negative.")
            return None
        return {
            "server_url": server_url,
            "client_path": self.client_path.get().strip(),
            "host_override": self.host.get().strip(),
            "gateway_port_override": gateway_port,
            "chat_port_override": chat_port,
            "instance_id_override": instance_id,
        }

    def _test(self) -> None:
        values = self._collect()
        if values is None:
            return
        client = self.config_store.client(server_url=values["server_url"])

        def done(document) -> None:
            self.status.configure(
                text=(
                    f"Connected to {document.get('name') or 'the server'} - "
                    f"{document.get('accounts', 0)} account(s), registration "
                    + ("open" if document.get("registration_open") else "closed")
                ),
                fg=OK,
            )

        self.run_async(client.status, done, busy="Contacting the service...")

    def _submit(self) -> None:
        values = self._collect()
        if values is None:
            return
        self.config_store.settings.update(values)
        self.config_store.save()
        self.result = True
        self.destroy()


class UiQueue:
    """Hands work back to the Tk thread through a polled queue.

    Calling ``after`` directly from a worker thread reaches into the Tcl
    interpreter from outside its thread, which raises as soon as the main
    thread is not sitting in the event loop. Everything crossing back into
    the UI goes through this instead.
    """

    def __init__(self, widget: tk.Misc, interval_ms: int = 40) -> None:
        self._widget = widget
        self._interval_ms = int(interval_ms)
        self._queue: queue.Queue = queue.Queue()
        self._poll()

    def post(self, callback) -> None:
        self._queue.put(callback)

    def _poll(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                traceback.print_exc()
        try:
            self._widget.after(self._interval_ms, self._poll)
        except tk.TclError:
            pass  # the window is gone; stop rescheduling


def ui_queue_for(widget: tk.Misc) -> UiQueue:
    root = widget._root()
    existing = getattr(root, "ui_queue", None)
    if existing is None:
        existing = UiQueue(root)
        root.ui_queue = existing
    return existing


def run_off_thread(widget: tk.Misc, work, on_success, on_error) -> None:
    """Run ``work`` in a thread and resume on the Tk thread."""

    poster = ui_queue_for(widget)

    def worker() -> None:
        try:
            result = work()
        except BaseException as exc:
            poster.post(lambda error=exc: on_error(error))
        else:
            poster.post(lambda value=result: on_success(value))

    threading.Thread(target=worker, daemon=True).start()


class LauncherWindow(tk.Tk):
    """The login window itself."""

    def __init__(self, config: ClientConfig) -> None:
        super().__init__()
        self.config_store = config
        # "checking" while the service is probed, then "online" or "offline";
        # the login form only exists in the first of those.
        self.state = "checking"
        self.title("Drakensang Online - login")
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        apply_dark_theme(self)

        self.canvas = tk.Canvas(
            self,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            highlightthickness=0,
            bd=0,
            bg=PANEL,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        paint_gradient(self.canvas, WINDOW_WIDTH, WINDOW_HEIGHT, BG_TOP, BG_BOTTOM)
        self._paint_frame()
        self._paint_logo()
        self._build_widgets()

        self.language = tk.StringVar(value=str(self.config_store.setting("language")))
        self._paint_language()
        last = str(self.config_store.settings.get("last_username", ""))
        if last:
            self.username.set(last)
        self._check_service()
        self.bind("<Return>", lambda _event: self._login())
        self.bind("<Escape>", lambda _event: self.destroy())

    # -- painting ---------------------------------------------------------

    def _paint_frame(self) -> None:
        self.canvas.create_rectangle(
            8, 8, WINDOW_WIDTH - 8, WINDOW_HEIGHT - 8,
            outline="#243640", width=1,
        )
        self.canvas.create_rectangle(
            11, 11, WINDOW_WIDTH - 11, WINDOW_HEIGHT - 11,
            outline="#16242c", width=1,
        )

    def _paint_logo(self) -> None:
        center = WINDOW_WIDTH / 2
        for step in range(14, 0, -1):
            self.canvas.create_oval(
                center - 14 * step, 96 - 4.2 * step,
                center + 14 * step, 96 + 4.2 * step,
                outline="",
                fill=_blend(BG_TOP, "#22333c", (15 - step) / 44),
            )
        for offset, color in ((2, "#31200a"), (1, "#7c5416")):
            self.canvas.create_text(
                center + offset, 92 + offset,
                text="DRAKENSANG", font=TITLE_FONT, fill=color,
            )
        self.canvas.create_text(
            center, 92, text="DRAKENSANG", font=TITLE_FONT, fill=GOLD
        )
        self.canvas.create_text(
            center, 124, text="O N L I N E", font=SUB_FONT, fill=GOLD_DIM
        )
        self.canvas.create_line(60, 146, WINDOW_WIDTH - 60, 146, fill="#243640")
        # The server's name, never its address: this window gets screenshotted
        # and streamed, and a visible address is an invitation to attack it.
        self.server_label = self.canvas.create_text(
            center, 162, text="", font=SMALL_FONT, fill=MUTED
        )

    def _set_server_label(self, text: str) -> None:
        self.canvas.itemconfigure(self.server_label, text=text)

    def _paint_language(self) -> None:
        box = self.canvas.create_rectangle(
            WINDOW_WIDTH - 108, 20, WINDOW_WIDTH - 22, 42,
            outline="#2b3f4a", fill="#132029",
        )
        text = self.canvas.create_text(
            WINDOW_WIDTH - 65, 31,
            text=f"▾  {self.language.get().upper()}",
            font=SMALL_FONT,
            fill=TEXT,
        )
        for item in (box, text):
            self.canvas.tag_bind(item, "<Button-1>", self._choose_language)
            self.canvas.tag_bind(
                item, "<Enter>", lambda _e: self.canvas.configure(cursor="hand2")
            )
            self.canvas.tag_bind(
                item, "<Leave>", lambda _e: self.canvas.configure(cursor="")
            )
        self._language_text = text

    def _choose_language(self, event) -> None:
        menu = tk.Menu(
            self, tearoff=0, bg=PANEL, fg=TEXT,
            activebackground="#24343d", activeforeground=GOLD, font=SMALL_FONT,
        )
        for code in LANGUAGES:
            menu.add_command(
                label=code.upper(),
                command=lambda value=code: self._set_language(value),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _set_language(self, code: str) -> None:
        self.language.set(code)
        self.canvas.itemconfigure(self._language_text, text=f"▾  {code.upper()}")
        self.config_store.settings["language"] = code
        self.config_store.save()

    # -- widgets ----------------------------------------------------------

    def _field(self, y: int, placeholder: str, *, secret: bool = False):
        self.canvas.create_rectangle(
            40, y, WINDOW_WIDTH - 40, y + 34,
            fill="#3a4a53", outline="#4d5f69", tags="form",
        )
        entry = PlaceholderEntry(self.canvas, placeholder, secret=secret)
        self.canvas.create_window(
            42, y + 2, anchor="nw", window=entry,
            width=WINDOW_WIDTH - 84, height=30, tags="form",
        )
        return entry

    def _build_widgets(self) -> None:
        self._build_login_form()
        self._build_waiting_state()
        self._build_offline_state()
        self._build_footer()

    def _build_login_form(self) -> None:
        self.username = self._field(200, "Username")
        self.password = self._field(242, "Password", secret=True)

        forgot = LinkLabel(
            self.canvas,
            "Change your password",
            self._change_password,
            color=GOLD_DIM,
            background=_blend(BG_TOP, BG_BOTTOM, 292 / WINDOW_HEIGHT),
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 292, window=forgot, tags="form"
        )

        self.login_button = GradientButton(
            self.canvas,
            "LOGIN",
            self._login,
            width=WINDOW_WIDTH - 80,
            height=46,
            background=_blend(BG_TOP, BG_BOTTOM, 338 / WINDOW_HEIGHT),
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 338, window=self.login_button, tags="form"
        )

        strip = _blend(BG_TOP, BG_BOTTOM, 386 / WINDOW_HEIGHT)
        self.launch_after_login = tk.BooleanVar(value=True)
        toggle = ToggleCheck(
            self.canvas,
            "Start the client after logging in",
            self.launch_after_login,
            background=strip,
            width=210,
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 386, window=toggle, tags="form"
        )

        self.status = tk.Label(
            self.canvas,
            text="",
            bg=_blend(BG_TOP, BG_BOTTOM, 428 / WINDOW_HEIGHT),
            fg=MUTED,
            font=SMALL_FONT,
            wraplength=WINDOW_WIDTH - 70,
            justify=tk.CENTER,
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 428, window=self.status,
            width=WINDOW_WIDTH - 70, tags="form",
        )

        register_strip = _blend(BG_TOP, BG_BOTTOM, 470 / WINDOW_HEIGHT)
        register = tk.Frame(self.canvas, bg=register_strip)
        tk.Label(
            register,
            text="Need an account?",
            bg=register_strip,
            fg=MUTED,
            font=SMALL_FONT,
        ).pack(side=tk.LEFT)
        LinkLabel(
            register,
            "Register now!",
            self._open_register,
            color=LINK,
            font=("Segoe UI", 9, "bold"),
            background=register_strip,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 470, window=register, tags="form"
        )

    def _build_waiting_state(self) -> None:
        """The startup screen: nothing to fill in until the server answers."""

        strip = _blend(BG_TOP, BG_BOTTOM, 300 / WINDOW_HEIGHT)
        self.spinner = Spinner(self.canvas, background=strip)
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 300, window=self.spinner, tags="waiting"
        )
        self.canvas.create_text(
            WINDOW_WIDTH / 2, 358,
            text="Connecting to the server...",
            font=("Segoe UI", 11),
            fill=TEXT,
            tags="waiting",
        )

    def _build_offline_state(self) -> None:
        self.canvas.create_text(
            WINDOW_WIDTH / 2, 296,
            text="Servers are currently down",
            font=("Segoe UI", 14, "bold"),
            fill=GOLD,
            tags="offline",
        )
        self.canvas.create_text(
            WINDOW_WIDTH / 2, 324,
            text="Please try again in a few minutes.",
            font=SMALL_FONT,
            fill=MUTED,
            tags="offline",
        )
        self.retry_button = GradientButton(
            self.canvas,
            "RETRY",
            self._check_service,
            width=170,
            height=40,
            background=_blend(BG_TOP, BG_BOTTOM, 372 / WINDOW_HEIGHT),
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 372, window=self.retry_button, tags="offline"
        )
        # The reason stays small and out of the way: it matters to whoever
        # configures the address, not to a player waiting for the server.
        self.offline_detail = tk.Label(
            self.canvas,
            text="",
            bg=_blend(BG_TOP, BG_BOTTOM, 432 / WINDOW_HEIGHT),
            fg="#5f6f78",
            font=("Segoe UI", 8),
            wraplength=WINDOW_WIDTH - 90,
            justify=tk.CENTER,
        )
        self.canvas.create_window(
            WINDOW_WIDTH / 2, 432, window=self.offline_detail,
            width=WINDOW_WIDTH - 90, tags="offline",
        )

    def _build_footer(self) -> None:
        """Shown in every state, so the address stays reachable when down."""

        self.canvas.create_line(60, 500, WINDOW_WIDTH - 60, 500, fill="#1e2c34")
        footer_strip = _blend(BG_TOP, BG_BOTTOM, 522 / WINDOW_HEIGHT)
        footer = tk.Frame(self.canvas, bg=footer_strip)
        LinkLabel(
            footer,
            "⚙  Settings",
            self._open_settings,
            color=MUTED,
            background=footer_strip,
        ).pack(side=tk.LEFT)
        self.logout_link = LinkLabel(
            footer,
            "Log out",
            self._logout,
            color=MUTED,
            background=footer_strip,
        )
        self.footer = footer
        self.canvas.create_window(WINDOW_WIDTH / 2, 522, window=footer)

    # -- behavior ---------------------------------------------------------

    def set_status(self, message: str, color: str = MUTED) -> None:
        self.status.configure(text=message, fg=color)

    def _busy(self, busy: bool) -> None:
        self.login_button.set_enabled(not busy)
        self.configure(cursor="watch" if busy else "")

    def _set_state(self, state: str) -> None:
        """Show exactly one of the waiting, offline, or login screens."""

        self.state = state
        visible = STATE_TAGS[state]
        for tag in STATE_TAGS.values():
            self.canvas.itemconfigure(
                tag, state="normal" if tag == visible else "hidden"
            )
        if state == "checking":
            self.spinner.start()
        else:
            self.spinner.stop()
        self._refresh_logout_link()

    def _refresh_logout_link(self) -> None:
        """Offer logging out only when there is a session to end."""

        session = self.config_store.session
        if session is not None and self.state == "online":
            self.logout_link.pack(side=tk.LEFT, padx=(16, 0))
        else:
            self.logout_link.pack_forget()

    def _check_service(self) -> None:
        """Ask the service whether it is up, and shape the window to match."""

        self._set_state("checking")
        self._set_server_label("")
        self._checking_since = time.monotonic()
        client = self.config_store.client()
        run_off_thread(
            self,
            client.status,
            lambda document: self._after_loader(self._service_is_up, document),
            lambda exc: self._after_loader(self._service_is_down, exc),
        )

    def _after_loader(self, callback, value) -> None:
        """Leave the loader up for its minimum, then hand over the result."""

        elapsed = time.monotonic() - getattr(self, "_checking_since", 0.0)
        remaining = MIN_LOADER_SECONDS - elapsed
        if remaining <= 0:
            callback(value)
            return
        self.after(int(remaining * 1000), lambda: callback(value))

    def _service_is_up(self, document: dict) -> None:
        self._set_state("online")
        self._set_server_label(str(document.get("name") or "Game server"))
        if self.username.get().strip():
            self.password.focus_set()
        else:
            self.username.focus_set()
        session = self.config_store.cached_session()
        if session is not None:
            self.set_status(
                f"Session for {session.username} is valid for "
                f"{format_duration(session.expires_at - time.time())}. "
                "Log in again to refresh it.",
                TEXT,
            )
            return
        registration = (
            "registration open"
            if document.get("registration_open")
            else "registration closed"
        )
        self.set_status(
            f"Connected - {document.get('accounts', 0)} account(s), "
            f"{registration}.",
            MUTED,
        )

    def _service_is_down(self, exc: BaseException) -> None:
        self._set_state("offline")
        self._set_server_label("")
        self.offline_detail.configure(text=describe_error(exc))

    def _login(self) -> None:
        if self.state != "online":
            return
        username = self.username.get().strip()
        if not username:
            self.set_status("Enter your username.", ERROR)
            return
        password = self.password.get()
        if not password:
            self.set_status("Enter your password.", ERROR)
            return
        client = self.config_store.client()
        self._busy(True)
        self.set_status(f"Logging in as {username}...", MUTED)

        def done(session) -> None:
            self._busy(False)
            self.password.set("")
            self.config_store.store_session(session)
            self._refresh_logout_link()
            if not self.launch_after_login.get():
                self.set_status(
                    f"Logged in as {session.username} (accid "
                    f"{session.account_id}); session valid for "
                    f"{format_duration(session.expires_at - time.time())}.",
                    OK,
                )
                return
            self._start_client(session)

        def failed(exc: BaseException) -> None:
            self._busy(False)
            if isinstance(exc, ServiceUnreachableError):
                # The server went away between the check and the attempt.
                self._service_is_down(exc)
                return
            self.set_status(describe_error(exc), ERROR)

        run_off_thread(
            self, lambda: client.login(username, password), done, failed
        )

    def _start_client(self, session) -> None:
        try:
            arguments = build_arguments(
                self.config_store, session, language=self.language.get()
            )
            executable = resolve_client_path(self.config_store, None)
            pid = start_client(executable, arguments)
        except (LauncherError, OSError) as exc:
            self.set_status(describe_error(exc), ERROR)
            return
        self.set_status(
            f"Launched {session.username} as pid {pid} (account "
            f"{session.account_id}). Connecting...",
            OK,
        )

    def _open_register(self) -> None:
        # Not ``_register``: Tk itself owns that name on the root window.
        session = RegisterDialog(self, self.config_store).show()
        if session is not None:
            self.username.set(session.username)
            self.password.set("")
            self._refresh_logout_link()
            self.set_status(
                f"Registered and logged in as {session.username} (accid "
                f"{session.account_id}).",
                OK,
            )
            if self.launch_after_login.get():
                self._start_client(session)

    def _change_password(self) -> None:
        username = PasswordDialog(
            self, self.config_store, self.username.get().strip()
        ).show()
        if username is not None:
            self._refresh_logout_link()
            self.set_status(
                f"Password changed for {username}; log in again.", OK
            )

    def _logout(self) -> None:
        session = self.config_store.session
        if session is None:
            return
        client = self.config_store.client()
        self.set_status(f"Logging out {session.username}...", MUTED)

        def done(_revoked) -> None:
            self.config_store.clear_session()
            self._refresh_logout_link()
            self.set_status(f"Logged out {session.username}.", MUTED)

        def failed(exc: BaseException) -> None:
            # The stored session is dropped either way; the server may still
            # hold it until it expires.
            self.config_store.clear_session()
            self._refresh_logout_link()
            self.set_status(describe_error(exc), ERROR)

        run_off_thread(
            self, lambda: client.logout(session.session_id), done, failed
        )

    def _open_settings(self) -> None:
        if SettingsDialog(self, self.config_store).show():
            # A changed address means the previous verdict no longer applies;
            # the check clears the server's name and shows the new one.
            self._check_service()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ensure_client_temp_directory()
    parser = argparse.ArgumentParser(
        prog="dso_launcher_gui.py", description="Graphical launcher."
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help="launcher settings (default: launcher-client.json)",
    )
    parser.add_argument("--server", help="account service URL to use")
    args = parser.parse_args(argv)
    try:
        config = (
            ClientConfig(Path(args.config_file))
            if args.config_file
            else ClientConfig()
        )
        if args.server:
            config.settings["server_url"] = normalize_server_url(args.server)
    except (LauncherError, AccountServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    LauncherWindow(config).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
