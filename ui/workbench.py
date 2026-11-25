"""Persistent composer and scrollable transcript, using prompt_toolkit as sole screen owner."""
import asyncio
import concurrent.futures
import io
import os
import shlex
import threading
import time
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.clipboard import InMemoryClipboard
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, VSplit, Window, Layout, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.containers import ScrollOffsets
from prompt_toolkit.layout.margins import ScrollbarMargin, ConditionalMargin
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.console import Console

from agent.events import AgentEvent
from config import Config
from ui.presentation import approval_handler, change_handler, change_record_handler
from ui.changes import ChangeStore
from ui.transcript import Transcript
from ui.logo import ICE_LOGO
from ui.clipboard import SystemClipboard
from ui.activity import activity_fragments, FRAME_INTERVAL
from llm.model_catalog import fetch_models


class TranscriptWindow(Window):
    """Route wheel movement through the same state as keyboard scrolling."""
    def __init__(self, *args, scroll, **kwargs):
        self.scroll_transcript = scroll
        super().__init__(*args, **kwargs)

    def _mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll_transcript(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll_transcript(3)
            return None
        return super()._mouse_handler(mouse_event)


class Workbench:
    def __init__(self, agent, input=None, output=None, clipboard=None):
        self.agent = agent
        self.transcript = Transcript()
        self.changes = ChangeStore()
        self.changes_view = None
        self.cancelled = threading.Event()
        self.task = None
        self.pending_approval = None
        self.approval_text = ""
        self.exit_pending = False
        self.notice = ""
        self.model_view = False
        self.model_loading = False
        self.model_catalog = []
        self.model_show_legacy = False
        self.pending_legacy_model = None
        self.input = TextArea(height=2, prompt="› ", multiline=True,
                              wrap_lines=True, history=InMemoryHistory(),
                              style="class:composer")
        self.log_control = FormattedTextControl(self.log_fragments, focusable=True,
                                               get_cursor_position=self.log_cursor)
        self.log_lines = 1
        self.follow = True
        self.scroll_top = 0
        self.log_window = TranscriptWindow(
            self.log_control, scroll=self.scroll, wrap_lines=False, always_hide_cursor=True,
            scroll_offsets=ScrollOffsets(top=0, bottom=0),
            get_vertical_scroll=lambda window: self.scroll_position,
            right_margins=[ConditionalMargin(
                ScrollbarMargin(display_arrows=True),
                filter=Condition(lambda: self.max_scroll > 0),
            )],
        )
        keys = KeyBindings()

        @keys.add("c-v", eager=True)
        @keys.add("s-insert", eager=True)
        def paste(event):
            try:
                value = self.app.clipboard.get_data().text
                self.input.buffer.insert_text(value)
                self.app.layout.focus(self.input)
                self.notice = ""
            except RuntimeError as exc:
                self.notice = str(exc)

        @keys.add("c-a", eager=True)
        def select_input(event):
            self.input.buffer.cursor_position = 0
            self.input.buffer.start_selection()
            self.input.buffer.cursor_position = len(self.input.text)

        @keys.add("f6", eager=True)
        def copy_response(event):
            self.copy_response()

        @keys.add("f7", eager=True)
        def copy_chat(event):
            self.copy_chat()

        @keys.add("enter")
        def submit(event):
            if self.pending_approval:
                self.resolve_approval(self.input.text.strip().lower() in {"y", "yes"})
                self.input.buffer.reset()
                return
            self.submit(self.input.text)

        @keys.add("escape", "enter")
        def newline(event):
            self.input.buffer.insert_text("\n")

        @keys.add("c-o")
        def details(event):
            self.transcript.toggle_details()

        @keys.add("pageup", eager=True)
        def scroll_up(event):
            self.scroll(-self.page_size)

        @keys.add("pagedown", eager=True)
        def scroll_down(event):
            self.scroll(self.page_size)

        @keys.add("s-up", eager=True)
        def line_up(event):
            self.scroll(-1)

        @keys.add("s-down", eager=True)
        def line_down(event):
            self.scroll(1)

        @keys.add("c-home", eager=True)
        def first_message(event):
            self.scroll(-self.log_lines)

        @keys.add("c-end", eager=True)
        def latest_message(event):
            self.scroll(self.log_lines)

        @keys.add("c-c")
        @keys.add("escape")
        def interrupt(event):
            if self.input.buffer.selection_state is not None:
                self.copy_text(self.input.buffer.copy_selection().text, "selection")
                return
            if self.model_view:
                self.model_view = False
                self.follow = True
                if self.model_loading and self.task is not None:
                    self.task.cancel()
                self.notice = "Model unchanged"
                return
            if self.changes_view is not None:
                self.changes_view = None
                self.follow = True
                return
            if self.pending_approval:
                self.resolve_approval(False)
            if self.transcript.busy:
                self.cancelled.set()
                self.notice = "Stopping after the current operation…"
                backend = getattr(self.agent, 'backend', None)
                if backend is not None:
                    asyncio.create_task(asyncio.to_thread(backend.cancel))
            else:
                self.input.buffer.reset()

        @keys.add("c-d")
        def quit_app(event):
            self.quit()

        styles = Style.from_dict({
            "": "#b9bec7", "muted": "#777e89", "border": "#444b55",
            "accent": "#9cb9c4", "composer": "bg:#1b1d20 #c4c9d1",
            "composer-meta": "bg:#1b1d20 #777e89", "status": "#777e89",
            "scrollbar.background": "#444b55", "scrollbar.button": "#9cb9c4",
            "scrollbar.arrow": "#777e89",
        })
        composer = HSplit([
            Window(FormattedTextControl(lambda: [("class:border", "─" * self.width)]), height=1),
            self.input,
            Window(FormattedTextControl(lambda: [("class:composer-meta", f"  {Config.MODEL}  ·  ICE" +
                    (" · sandbox" if getattr(self.agent, 'backend', None) is not None else ""))]),
                   height=1, style="class:composer-meta"),
            Window(FormattedTextControl(lambda: [("class:border", "─" * self.width)]), height=1),
        ])
        self.activity_window = Window(FormattedTextControl(self.activity_fragments), height=1)
        body = HSplit([
            Window(FormattedTextControl(lambda: [("class:muted", str(Path.cwd()))]),
                   height=1, align="right"),
            self.log_window,
            ConditionalContainer(Window(FormattedTextControl(self.approval_fragments),
                                        height=7, wrap_lines=True),
                                 filter=Condition(lambda: self.pending_approval is not None)),
            ConditionalContainer(self.activity_window,
                filter=Condition(lambda: self.transcript.busy and not self.transcript.failed
                                 and self.pending_approval is None)),
            composer,
            Window(FormattedTextControl(self.status_fragments), height=1),
            Window(height=1),
        ], width=Dimension(preferred=110, max=110))
        self.app = Application(layout=Layout(VSplit([
            Window(width=Dimension(min=3, weight=1)), body,
            Window(width=Dimension(min=3, weight=1))
        ]), focused_element=self.input), key_bindings=keys, style=styles,
            full_screen=True, mouse_support=True, refresh_interval=FRAME_INTERVAL,
            clipboard=clipboard if clipboard is not None else (
                SystemClipboard() if os.name == "nt" else InMemoryClipboard()),
            input=input, output=output)

    def copy_text(self, value, label):
        if not value:
            self.notice = "Nothing to copy yet"
            return
        try:
            self.app.clipboard.set_text(value)
            self.notice = f"Copied {label}"
        except RuntimeError as exc:
            self.notice = str(exc)

    def copy_response(self):
        for entry in reversed(self.transcript.entries):
            if isinstance(entry, dict) and "text" in entry:
                self.copy_text(entry["text"], "response")
                return
        self.notice = "No response to copy yet"

    def copy_chat(self):
        buffer = io.StringIO()
        Console(file=buffer, width=self.width, highlight=False, color_system=None).print(
            self.transcript.render(self.width))
        self.copy_text(buffer.getvalue(), "chat")

    @property
    def width(self):
        return max(10, min(110, self.app.output.get_size().columns - 6))

    def log_cursor(self):
        y = self.log_lines - 1 if self.follow else self.scroll_position
        return Point(x=0, y=max(0, min(y, self.log_lines - 1)))

    @property
    def page_size(self):
        info = self.log_window.render_info
        return max(1, (info.window_height if info else self.app.output.get_size().rows - 8) - 1)

    @property
    def max_scroll(self):
        return max(0, self.log_lines - (self.page_size + 1))

    @property
    def scroll_position(self):
        return self.max_scroll if self.follow else min(self.scroll_top, self.max_scroll)

    def scroll(self, delta):
        target = max(0, min(self.max_scroll, self.scroll_position + delta))
        self.scroll_top = target
        self.follow = target == self.max_scroll
        self.app.invalidate()

    def log_fragments(self):
        if self.model_view:
            fragments = [('class:accent', f'\nGemini models · current: {Config.MODEL}\n'),
                ('class:muted', 'Enter a number or exact model ID · a includes legacy · Esc cancels\n\n')]
            if self.model_loading:
                fragments.append(('class:muted', 'Fetching models from Google…\n'))
            else:
                for number, choice in enumerate(self.visible_models(), 1):
                    fragments.extend([('', f'{number:3}. {choice.model_id}'),
                                      ('class:muted', f'  {choice.status}\n')])
            self.log_lines = sum(value.count('\n') for _, value in fragments) + 1
            return fragments
        if not self.transcript.entries and self.changes_view is None:
            fragments = [("", "\n"),
                    ("class:accent", "\n".join("  " + line for line in ICE_LOGO.splitlines()) + "\n"),
                    ("class:muted", "  Interactive Coding Environment\n\n  Ask anything to get started.\n")]
            self.log_lines = sum(text.count("\n") for _, text in fragments) + 1
            return fragments
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, color_system="truecolor",
                          width=max(1, self.width - 1), highlight=False)
        console.print(self.changes.render(self.changes_view) if self.changes_view is not None
                      else self.transcript.render(self.width - 1))
        value = buffer.getvalue()
        self.log_lines = value.count("\n") + 1
        return to_formatted_text(ANSI(value))

    def status_fragments(self):
        if self.pending_approval:
            left = "Approval required · type y or n, then enter"
        elif self.notice:
            left = self.notice
        elif self.transcript.busy:
            left = "Working"
        else:
            left = "Ready"
        if self.transcript.tokens:
            left += f" · {self.transcript.tokens:,} tokens"
        backend = getattr(self.agent, 'backend', None)
        if backend is not None:
            left += f" · {len(backend.workspace.pending())} pending files"
            if backend.network_enabled:
                left += " · network ON (installing)"
        if not self.follow:
            left += " · scrollback (ctrl+end for latest)"
        right = "ctrl+o details · esc stop" if self.transcript.busy else "/changes · ctrl+o details · ctrl+d quit"
        if self.changes_view is not None:
            right = "/changes — files · /chat or Esc — return"
        available = self.width - len(left) - len(right)
        return [("class:status", left + (" " * available + right if available > 2 else ""))]

    def activity_fragments(self):
        if not self.transcript.busy or self.transcript.failed or self.pending_approval is not None:
            return []
        if self.cancelled.is_set():
            label = "Stopping…"
        elif self.transcript.active:
            labels = {"read_file": "Reading…", "write_file": "Writing…",
                      "edit_file": "Editing…", "run_command": "Running…",
                      "list_directories": "Looking around…", "grep": "Searching…",
                      "glob": "Searching…", "plan": "Planning…",
                      "install_dependencies": "Installing dependencies…"}
            label = labels.get(self.transcript.active.name, "Working…")
        elif self.transcript.streaming is not None:
            label = "Responding…"
        else:
            label = "Thinking…"
        return activity_fragments(label, time.monotonic() - self.transcript.started,
                                  self.transcript.tokens)

    def approval_fragments(self):
        return [("class:accent", "Permission required\n"),
                ("", self.approval_text[:self.width * 6])]

    def resolve_approval(self, approved):
        pending = self.pending_approval
        self.pending_approval = None
        if pending and not pending.done():
            pending.set_result(approved)

    def quit(self):
        if self.model_loading:
            if self.task is not None:
                self.task.cancel()
            self.app.exit()
            return
        if self.transcript.busy or (self.task is not None and not self.task.done()):
            self.exit_pending = True
            self.cancelled.set()
            self.resolve_approval(False)
            self.notice = "Stopping after the current operation…"
            backend = getattr(self.agent, 'backend', None)
            if backend is not None:
                asyncio.create_task(asyncio.to_thread(backend.cancel))
        else:
            self.app.exit()

    def submit(self, message):
        message = message.strip()
        if not message:
            return
        command, _, selection = message.partition(" ")
        if self.model_view and not message.startswith('/'):
            self.input.buffer.reset()
            self.choose_model(message)
            return
        if command.lower() in {"/changes", "/chat"}:
            self.model_view = False
            self.input.buffer.reset(append_to_history=True)
            self.changes_view = selection.strip() if command.lower() == "/changes" else None
            self.follow = self.changes_view is None
            self.scroll_top = 0
            self.notice = ""
            return
        if self.transcript.busy or (self.task is not None and not self.task.done()):
            self.notice = "Current task is running · esc to stop"
            return
        self.notice = ""
        self.input.buffer.reset(append_to_history=True)
        if command.lower() == '/model':
            self.model_view = True
            self.model_loading = True
            self.model_show_legacy = False
            self.pending_legacy_model = None
            self.follow = False
            self.scroll_top = 0
            self.task = asyncio.create_task(self.load_models(selection.strip()))
            return
        if command.lower() == '/install':
            backend = getattr(self.agent, 'backend', None)
            if backend is None:
                self.notice = "Dependency installation requires an active Docker sandbox"
                return
            try:
                options = shlex.split(selection)
                if len(options) > 2 or (options and options[0] not in {'auto', 'npm', 'pip'}):
                    raise ValueError
            except ValueError:
                self.notice = 'Usage: /install [auto|npm|pip] [project-directory]'
                return
            arguments = {'manager': options[0] if options else 'auto',
                         'path': options[1] if len(options) > 1 else '.'}
            self._start_turn(message, ('install_dependencies', arguments))
            return
        if message in {'/apply', '/sandbox'}:
            backend = getattr(self.agent, 'backend', None)
            if backend is None:
                self.notice = "No sandbox backend is active"
            elif message == '/sandbox':
                self.notice = backend.description()
            else:
                try:
                    count = len(backend.apply())
                    self.notice = f"Applied {count} files to the host workspace"
                    instructions = backend.local_run_instructions()
                    if count and instructions:
                        self.transcript.entries.append({'text': 'Changes applied.\n\n' + instructions})
                        self.changes_view = None
                        self.follow = True
                except Exception as exc:
                    self.notice = str(exc)
                    self.transcript.entries.append({'error': str(exc)})
            return
        if message.lower() in {"exit", "/quit", "/exit"}:
            self.quit()
            return
        if message == "/expand":
            self.transcript.toggle_details()
            return
        if message == "/copy":
            self.copy_chat()
            return
        if message == "/help":
            self.transcript.entries.append({"text":
                "/expand or Ctrl+O — toggle latest tool details\n"
                "/changes — session files · /changes N — file · /changes N M — individual edit\n"
                "/changes all — combined diffs · /chat or Esc — return to chat\n"
                "/sandbox — isolation status · /apply — apply staged changes to host files\n"
                "/install [auto|npm|pip] [directory] — approve a temporary network install\n"
                "/model — choose a Gemini model while idle; conversation and files are preserved\n"
                "Alt+Enter — newline · Wheel or PageUp/PageDown — scroll\n"
                "Shift+Up/Down — scroll a line · Ctrl+Home/End — first/latest\n"
                "Ctrl+V or Shift+Insert — paste · Ctrl+A then Ctrl+C — copy input\n"
                "F6 — copy latest response · F7 or /copy — copy chat\n"
                "Esc or Ctrl+C — stop · Ctrl+D or /exit — quit"})
            return
        self._start_turn(message)

    def visible_models(self):
        return [model for model in self.model_catalog
                if self.model_show_legacy or not model.status.startswith('Legacy')]

    async def load_models(self, requested=''):
        try:
            self.model_catalog = await asyncio.to_thread(fetch_models, Config.API_KEY)
            self.notice = 'Choose a model number or ID' if self.model_catalog else 'No eligible Gemini models returned'
        except asyncio.CancelledError:
            return
        except Exception:
            self.model_view = False
            self.notice = 'Could not fetch Gemini models. Current model unchanged; check key/network and retry /model.'
        finally:
            self.model_loading = False
            self.app.invalidate()
        if requested and self.model_view:
            self.choose_model(requested)

    def choose_model(self, answer):
        if self.model_loading:
            self.notice = 'Still fetching models…'
            return
        if answer.lower() in {'q', 'quit', 'exit'}:
            self.model_view = False
            self.follow = True
            self.notice = 'Model unchanged'
            return
        if answer.lower() == 'a':
            self.model_show_legacy = True
            return
        visible = self.visible_models()
        model_id = answer.removeprefix('models/')
        choice = next((model for model in visible if model.model_id == model_id), None)
        if answer.isdecimal() and 1 <= int(answer) <= len(visible):
            choice = visible[int(answer) - 1]
        if choice is None:
            self.notice = 'Choose a listed number or exact model ID'
            return
        if choice.status.startswith('Legacy') and self.pending_legacy_model != choice.model_id:
            self.pending_legacy_model = choice.model_id
            self.notice = 'Deprecated model: repeat this selection to confirm, or choose another model'
            return
        try:
            self.agent.client.switch_model(choice.model_id)
        except Exception:
            self.notice = 'Could not switch models. Current chat and model retained.'
            return
        self.model_view = False
        self.follow = True
        self.notice = f'Using {choice.model_id} · conversation and sandbox preserved'

    def _start_turn(self, message, tool_request=None):
        self.model_view = False
        self.follow = True
        self.changes_view = None
        self.cancelled.clear()
        self.transcript.handle(AgentEvent.agent_start(message))
        self.task = asyncio.create_task(self.run_turn(message, tool_request))

    async def run_turn(self, message, tool_request=None):
        loop = asyncio.get_running_loop()

        def receive(event):
            # AGENT_START was handled before launching the worker.
            if event.type.value != "agent_start":
                self.transcript.handle(event)
            self.app.invalidate()

        def approve(command, result):
            future = concurrent.futures.Future()
            def show():
                if self.cancelled.is_set():
                    future.set_result(False)
                    return
                self.pending_approval = future
                self.approval_text = result.reason + '\n' + command
                self.app.invalidate()
            loop.call_soon_threadsafe(show)
            return future.result()

        def change(path, diff, created):
            loop.call_soon_threadsafe(self.transcript.change, path, diff, created)

        def record(path, before, after, created):
            loop.call_soon_threadsafe(self.changes.record, path, before, after, created)

        def direct_tool_events():
            name, arguments = tool_request
            yield AgentEvent.tool_call_start(name, arguments)
            result = self.agent.backend.invoke(name, arguments)
            yield AgentEvent.tool_call_complete(name, result)
            yield AgentEvent.agent_end()

        def worker():
            change_token = change_handler.set(change)
            approval_token = approval_handler.set(approve)
            record_token = change_record_handler.set(record)
            events = direct_tool_events() if tool_request is not None else self.agent.run(message, stream=True)
            try:
                for event in events:
                    if self.cancelled.is_set():
                        break
                    loop.call_soon_threadsafe(receive, event)
            except Exception as exc:
                loop.call_soon_threadsafe(receive, AgentEvent.agent_error(str(exc)))
            finally:
                events.close()
                change_handler.reset(change_token)
                approval_handler.reset(approval_token)
                change_record_handler.reset(record_token)

        await loop.run_in_executor(None, worker)
        self.transcript.busy = False
        if self.transcript.active:
            self.transcript.active.status = "Stopped"
            self.transcript.active = None
        self.notice = "Stopped" if self.cancelled.is_set() else ""
        self.resolve_approval(False)
        self.app.invalidate()
        if self.exit_pending:
            self.app.exit()

    def run(self):
        self.app.run()
