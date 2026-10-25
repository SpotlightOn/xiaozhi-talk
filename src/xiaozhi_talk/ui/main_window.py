"""Main window: menu bar, chat log, composer and status bar."""

from __future__ import annotations

import logging
import math
import time
from collections import deque

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..audio import MULTIMEDIA_AVAILABLE, VoicePlayer
from ..audio_input import MULTIMEDIA_AVAILABLE as CAPTURE_AVAILABLE
from ..audio_input import MicrophoneCapture
from ..bridge import ClientBridge
from ..client import PLAYBACK_SAMPLE_RATE
from ..storage import CredentialStore, StorageError, Vault
from ..voice import PAUSE_FRAMES, PREBUFFER_FRAMES, SPEECH_FRAMES, is_speech
from .accounts_dialog import SettingsAccountsDialog
from .chat_view import ChatView
from .composer import Composer
from .log_viewer import LogBuffer, LogViewerDialog
from .mic_button import IDLE_TOOLTIP, RECORDING_TOOLTIP
from .setup_wizard import SetupWizard

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Xiaozhi Talk"

#: A recording quieter than this (-46 dBFS) counts as silence.
SILENT_PEAK = 0.005

#: Delays in seconds between automatic reconnect attempts.
RECONNECT_DELAYS = (1, 2, 5, 10, 20, 30)


def _level_meter(level: float, blocks: int = 8) -> str:
    """Draw the microphone level as filled and empty blocks."""
    if level <= 0.0:
        filled = 0
    else:
        decibel = 20.0 * math.log10(max(level, 1e-5))
        fraction = min(1.0, max(0.0, (decibel + 60.0) / 60.0))
        filled = max(1, round(fraction * blocks))
    return "▮" * filled + "▯" * (blocks - filled)


def _shorten(text: str, limit: int = 70) -> str:
    """Trim ``text`` for the status bar; the chat keeps the full message."""
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 1].rstrip() + "…"


class MainWindow(QMainWindow):
    def __init__(
        self,
        store: CredentialStore,
        vault: Vault,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.vault = vault
        self._connected = False
        self._connecting = False
        self._streaming = False
        self._source_actions: dict[str, QAction] = {}
        self._qt_settings = QSettings("xiaozhi-talk", "xiaozhi-talk")

        # Connection log: collected from the start, shown on demand.
        self._log_buffer = LogBuffer(parent=self)
        logging.getLogger().addHandler(self._log_buffer)
        self._log_dialog: LogViewerDialog | None = None

        # Automatic reconnect after a dropped connection.
        self._manual_disconnect = False
        self._ever_connected = False
        self._reconnect_attempt = 0
        self._closing = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._reconnect)

        self._bridge = ClientBridge(self)
        self._player: VoicePlayer | None = None
        if MULTIMEDIA_AVAILABLE:
            self._player = VoicePlayer(self)

        self._audio_timer = QTimer(self)
        self._audio_timer.setInterval(25)
        self._audio_timer.timeout.connect(self._pump_audio)

        # Voice input: the capture is created on first use so that no audio
        # device is opened for people who only type.
        self._capture: MicrophoneCapture | None = None
        self._recording = False
        self._pending_capture = False
        self._pending_frames: list[bytes] = []
        self._record_started_at = 0.0
        self._record_level = 0.0
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(250)
        self._record_timer.timeout.connect(self._update_record_status)

        # Hands-free mode: the microphone stays open, speech starts a message
        # and two seconds of quiet send it.
        self._hands_free = False
        self._muted = False
        self._resume_pending = False
        self._prebuffer: deque[bytes] = deque(maxlen=PREBUFFER_FRAMES)
        self._hf_speech = 0
        self._hf_silence = 0
        self._hf_waiting = False

        self._build_ui()
        self._build_menus()
        self._connect_bridge()
        self._refresh_accounts_menu()
        self._apply_settings()
        self._restore_geometry()
        self._update_connection_actions()

        if self.vault.active_account() is None:
            self.chat.add_notice(
                "No account configured yet — open Settings → Manage settings and accounts…"
            )
        elif self.vault.settings.auto_connect:
            QTimer.singleShot(0, self.connect_active)

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(980, 800)
        self.setMinimumSize(560, 420)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.chat = ChatView()
        self.composer = Composer()
        self.splitter.addWidget(self.chat)
        self.splitter.addWidget(self.composer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([660, 130])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.splitter)
        self.setCentralWidget(central)

        self._status_label = QLabel("Not connected")
        self._account_label = QLabel("")
        self.statusBar().addWidget(self._status_label, 1)
        self.statusBar().addPermanentWidget(self._account_label)

        self.composer.sendRequested.connect(self.send_message)
        self.composer.micRequested.connect(self.toggle_recording)
        self.composer.auto_speak.setChecked(self.vault.settings.auto_speak)
        self.composer.autoSpeakToggled.connect(self._on_auto_speak_toggled)
        self.composer.muteToggled.connect(self._on_mute_toggled)
        self.composer.setFocus()
        self.chat.verticalScrollBar()

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        session_menu = menu_bar.addMenu("&Session")
        self._connect_action = QAction("&Connect", self)
        self._connect_action.setShortcut(QKeySequence("Ctrl+R"))
        self._connect_action.triggered.connect(self.connect_active)
        session_menu.addAction(self._connect_action)

        self._disconnect_action = QAction("&Disconnect", self)
        self._disconnect_action.setShortcut(QKeySequence("Ctrl+D"))
        self._disconnect_action.triggered.connect(self.disconnect_session)
        session_menu.addAction(self._disconnect_action)

        self._stop_action = QAction("&Stop reply", self)
        self._stop_action.setShortcut(QKeySequence("Ctrl+."))
        self._stop_action.triggered.connect(self.stop_reply)
        session_menu.addAction(self._stop_action)

        self._voice_action = QAction("&Record a voice message", self)
        self._voice_action.setShortcut(QKeySequence("Ctrl+Shift+Space"))
        self._voice_action.triggered.connect(self.toggle_recording)
        session_menu.addAction(self._voice_action)

        self._mute_action = QAction("&Mute microphone", self, checkable=True)
        self._mute_action.setShortcut(QKeySequence("Ctrl+M"))
        self._mute_action.toggled.connect(self._on_mute_action)
        session_menu.addAction(self._mute_action)

        session_menu.addSeparator()
        clear_action = QAction("C&lear chat", self)
        clear_action.setShortcut(QKeySequence("Ctrl+L"))
        clear_action.triggered.connect(self.chat.clear_messages)
        session_menu.addAction(clear_action)

        session_menu.addSeparator()
        log_action = QAction("Connection &log…", self)
        log_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        log_action.triggered.connect(self.show_connection_log)
        session_menu.addAction(log_action)

        session_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        session_menu.addAction(quit_action)

        settings_menu = menu_bar.addMenu("&Settings")

        manage_action = QAction("&Manage settings and accounts…", self)
        manage_action.setShortcut(QKeySequence("Ctrl+,"))
        manage_action.triggered.connect(self.manage_settings)
        settings_menu.addAction(manage_action)

        self._accounts_menu = settings_menu.addMenu("&Account")

        settings_menu.addSeparator()
        self._audio_action = QAction("Play &voice replies", self, checkable=True)
        self._audio_action.toggled.connect(self._on_audio_toggled)
        settings_menu.addAction(self._audio_action)

        help_menu = menu_bar.addMenu("&Help")
        shortcuts_action = QAction("&Keyboard shortcuts", self)
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)
        about_action = QAction("&About Xiaozhi Talk", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _connect_bridge(self) -> None:
        self._bridge.statusChanged.connect(self._set_status)
        self._bridge.connected.connect(self._on_connected)
        self._bridge.disconnected.connect(self._on_disconnected)
        self._bridge.userMessage.connect(self.chat.add_user_message)
        self._bridge.replyStarted.connect(self._on_reply_started)
        self._bridge.replyChunk.connect(self.chat.append_reply)
        self._bridge.replyFinished.connect(self._on_reply_finished)
        self._bridge.emotionChanged.connect(self.chat.set_emotion)
        self._bridge.notice.connect(self.chat.add_notice)
        self._bridge.errorOccurred.connect(self._on_error)
        self._bridge.speechReady.connect(self._on_speech_ready)

    def _restore_geometry(self) -> None:
        geometry = self._qt_settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # -- connection ---------------------------------------------------------

    def connect_active(self) -> None:
        if self._connecting:
            return
        self._manual_disconnect = False
        account = self.vault.active_account()
        if account is None:
            self.chat.add_notice(
                "No account configured yet — open Settings → Manage settings and accounts…"
            )
            return
        problems = account.problems()
        if problems:
            self.chat.add_notice(f'The account "{account.name}" is incomplete: {problems[0]}')
            return

        self._cancel_recording()
        self._bridge.stop()
        self._connected = False
        self._streaming = False
        self._player_stop()
        self._connecting = True
        self._set_status("Connecting…")
        self._account_label.setText(account.name)
        self._update_connection_actions()
        self._bridge.start(account)

    def disconnect_session(self) -> None:
        self._manual_disconnect = True
        self._reconnect_timer.stop()
        self._stop_hands_free()
        self._cancel_recording()
        self._bridge.stop()
        self._connected = False
        self._connecting = False
        self._streaming = False
        self._player_stop()
        self._set_status("Disconnected")
        self._update_connection_actions()

    def stop_reply(self) -> None:
        self._cancel_recording()
        self._bridge.abort()
        self.chat.finish_reply()
        if self._connected:
            self._set_status("Connected")

    def send_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if not self._connected:
            self.chat.add_notice("Not connected. Use Session → Connect (Ctrl+R).")
            self.composer.set_text(text)
            return
        self.chat.add_user_message(text)
        self._bridge.send_text(text)
        self.composer.focus_editor()

    def _on_connected(self) -> None:
        self._connected = True
        self._connecting = False
        self._ever_connected = True
        self._reconnect_attempt = 0
        self._reconnect_timer.stop()
        account = self._bridge.account
        name = account.name if account is not None else "Xiaozhi"
        self._set_status(f"Connected to {name}")
        self._account_label.setText(name)
        self.setWindowTitle(f"{WINDOW_TITLE} — {name}")
        if self.vault.settings.clear_chat_on_connect:
            self.chat.clear_messages()
        if self.vault.settings.play_audio:
            self._audio_timer.start()
        self._mute_action.setEnabled(CAPTURE_AVAILABLE)
        if self._hands_free_enabled and not self._muted:
            self._start_hands_free()
        self._update_connection_actions()

    def _on_disconnected(self, reason: str) -> None:
        self._cancel_recording()
        self._hands_free = False
        self._resume_pending = False
        was_connected = self._connected
        self._connected = False
        self._connecting = False
        self._streaming = False
        self._player_stop()
        self._set_status("Disconnected")
        self.setWindowTitle(WINDOW_TITLE)
        if was_connected:
            self.chat.add_notice(reason)
        self._update_connection_actions()
        self._schedule_reconnect()

    def _on_error(self, message: str) -> None:
        if self._connecting:
            # A failed connection attempt is not a connected session: report it
            # and offer Connect again instead of staying in "Connecting…".
            self._connecting = False
            self._set_status("Not connected")
            self._update_connection_actions()
        else:
            self._set_status(f"Error: {_shorten(message)}")
        self.chat.add_notice(message, error=True)

    def _stop_recording_for_server_turn(self) -> None:
        """The server already answered, so the recording has served its purpose."""
        self._recording = False
        self._pending_capture = False
        self._pending_frames = []
        self._record_timer.stop()
        self.composer.set_recording(False)
        if self._capture is not None and self._capture.recording:
            self._capture.stop()
        self._bridge.end_speech()
        if self._connected:
            self._set_status("Connected")

    def _on_reply_started(self) -> None:
        if self._recording or self._pending_capture:
            # The server's own voice detection ended the turn, so the
            # microphone must stop before the reply is played back.
            self._stop_recording_for_server_turn()
        self._streaming = True
        self._update_connection_actions()
        self.chat.start_reply()
        if self._connected:
            self._set_status("Xiaozhi is replying…")

    def _on_reply_finished(self) -> None:
        self._streaming = False
        self._update_connection_actions()
        self.chat.finish_reply()
        if self._hands_free:
            # The microphone stays quiet until the reply has been played, or it
            # would record Xiaozhi and start another turn.  The timer is a
            # safety net: without playback there is nothing to wait for.
            self._resume_pending = True
            QTimer.singleShot(4000, self._resume_hands_free)
        elif self._connected:
            self._set_status("Connected")

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def _update_connection_actions(self) -> None:
        """Keep the session menu in step with the connection state.

        Only one of Connect and Disconnect makes sense at a time, and Stop only
        while a reply is actually streaming.
        """
        self._connect_action.setEnabled(not self._connected and not self._connecting)
        self._disconnect_action.setEnabled(self._connected or self._connecting)
        self._stop_action.setEnabled(self._streaming)

        # The microphone is blocked while a reply streams: an open microphone
        # would record the speakers.
        busy = self._recording or self._pending_capture
        can_record = (
            self._connected and not self._streaming and not self._hands_free and CAPTURE_AVAILABLE
        )
        self._voice_action.setEnabled(can_record or busy)
        self._voice_action.setText("&Stop recording" if busy else "&Record a voice message")
        self._mute_action.setEnabled(self._connected and CAPTURE_AVAILABLE)

        button = self.composer.mic_button
        button.setEnabled(can_record or busy)
        if can_record or busy:
            button.setToolTip(RECORDING_TOOLTIP if button.recording else IDLE_TOOLTIP)
        else:
            button.setToolTip(self._mic_disabled_reason())

    # -- voice input --------------------------------------------------------

    def toggle_recording(self) -> None:
        """Start a recording, or finish and send the running one."""
        if self._hands_free:
            self.chat.add_notice(
                "Hands-free mode is on: just speak. Untick Auto-speak to record a message."
            )
            return
        if self._recording or self._pending_capture:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self._recording or self._pending_capture:
            return
        if not CAPTURE_AVAILABLE:
            self.chat.add_notice(
                "Voice input needs Qt Multimedia, which is not installed on this computer."
            )
            return
        if not self._connected:
            self.chat.add_notice("Not connected. Use Session → Connect (Ctrl+R).")
            return
        if self._streaming:
            self.chat.add_notice("Wait until Xiaozhi has finished the reply before recording.")
            return

        capture = self._capture_instance()
        if not capture.start(self.vault.settings.input_device):
            return  # the reason was reported through ``failed``

        # The microphone records right away; its first frames wait here until
        # the server listens, so no word is lost while that is arranged.
        self._pending_frames = []
        self._pending_capture = True
        self._recording = True
        self._record_started_at = time.monotonic()
        self.composer.set_recording(True)
        self._record_timer.start()
        self._update_record_status()
        self._update_connection_actions()
        self._bridge.start_speech()
        # A client that never answers must not leave the button stuck.
        QTimer.singleShot(3000, self._abandon_pending_capture)

    def _on_speech_ready(self, ready: bool) -> None:
        """The client either listens now or refused to start."""
        if self._hands_free:
            if not ready:
                # The client refused this utterance; try the next one.
                self._cancel_recording()
                self._hf_waiting = False
                return
        elif not self._pending_capture:
            if ready:
                # Started too late, the recording is already over.
                self._bridge.cancel_speech()
            return
        self._pending_capture = False
        if not ready:
            self._cancel_recording()
            self._set_status("Connected" if self._connected else "Not connected")
            self._update_connection_actions()
            return
        frames, self._pending_frames = self._pending_frames, []
        for frame in frames:
            self._bridge.push_audio(frame)

    def _abandon_pending_capture(self) -> None:
        if not self._pending_capture:
            return
        self._cancel_recording()
        self.chat.add_notice(
            "The microphone could not be started. Check the connection and try again.",
            error=True,
        )
        self._set_status("Connected" if self._connected else "Not connected")
        self._update_connection_actions()

    def stop_recording(self, *, keep_capture: bool = False) -> None:
        if not self._recording and not self._pending_capture:
            return
        self._recording = False
        self._record_timer.stop()
        self.composer.set_recording(False)
        # Stopping the capture emits the last, padded frame before the client
        # adds the trailing silence, so nothing is lost here.
        if self._capture is not None and not keep_capture:
            self._capture.stop()
        peak = getattr(self._capture, "peak", 1.0)
        if not self._pending_capture and peak < SILENT_PEAK:
            # Almost no signal: the headset is muted, the wrong device is
            # selected, or nobody spoke. Saying so saves a puzzled minute.
            self.chat.add_notice(
                "The recording was almost silent. Check that the microphone is not "
                "muted and that the right device is chosen under Settings → Voice input.",
                error=True,
            )
        if self._pending_capture:
            # The server never started listening, so nothing was sent.
            self._pending_capture = False
            self._pending_frames = []
            self._bridge.cancel_speech()
        else:
            self._bridge.stop_speech()
        if self._connected:
            self._set_status("Connected")
        self._update_connection_actions()

    def _capture_instance(self) -> MicrophoneCapture:
        """The capture object, created and wired on first use."""
        capture = self._capture
        if capture is None:
            capture = MicrophoneCapture(self)
            capture.frameReady.connect(self._on_capture_frame)
            capture.level.connect(self._on_capture_level)
            capture.failed.connect(self._on_capture_failed)
            self._capture = capture
        return capture

    def _on_capture_level(self, level: float) -> None:
        """Remember how loud the microphone is; the status bar shows it."""
        # A little smoothing, otherwise the bar jumps on every syllable.
        self._record_level = max(level, self._record_level * 0.6)
        if self._hands_free and not self._recording and not self._streaming:
            self._set_status(f"Listening…   {_level_meter(self._record_level)}")

    def _on_capture_frame(self, frame: bytes) -> None:
        if self._hands_free and not self._pending_capture:
            self._hands_free_frame(frame)
            return
        if self._pending_capture:
            self._pending_frames.append(frame)
            return
        self._bridge.push_audio(frame)

    def _hands_free_frame(self, frame: bytes) -> None:
        """Hands-free: notice speech, then send the message after a pause."""
        if self._recording:
            self._bridge.push_audio(frame)
            if is_speech(frame):
                self._hf_speech += 1
                self._hf_silence = 0
            else:
                self._hf_silence += 1
                if self._hf_silence >= PAUSE_FRAMES and self._hf_speech >= 2:
                    self._end_hands_free_utterance()
            return

        if self._hf_waiting and not self._streaming:
            # The message is on its way; wait for the reply to begin.
            return

        if is_speech(frame):
            self._hf_speech += 1
            self._prebuffer.append(frame)
            if self._hf_speech >= SPEECH_FRAMES:
                self._begin_hands_free_utterance()
        else:
            self._hf_speech = 0
            self._prebuffer.append(frame)

    def _on_capture_failed(self, message: str) -> None:
        self._hands_free = False
        self._resume_pending = False
        self._cancel_recording()
        self.chat.add_notice(message, error=True)
        self._set_status("Connected" if self._connected else "Not connected")
        self._update_connection_actions()

    def _cancel_recording(self) -> None:
        """Drop a running recording without sending it."""
        was_recording = self._recording or self._pending_capture
        self._hf_speech = 0
        self._hf_silence = 0
        self._prebuffer.clear()
        self._recording = False
        self._pending_capture = False
        self._pending_frames = []
        self._record_timer.stop()
        self.composer.set_recording(False)
        if self._capture is not None and self._capture.recording:
            self._capture.stop()
        if was_recording:
            self._bridge.cancel_speech()

    # -- hands-free mode ----------------------------------------------------

    def _on_auto_speak_toggled(self, enabled: bool) -> None:
        """Turn the always-listening conversation mode on or off."""
        self.vault.settings.auto_speak = enabled
        if enabled:
            # "Auto-speak" means the reply is spoken too, so make sure it is.
            self.vault.settings.play_audio = True
        self._save_vault()
        self._apply_settings()

        self._mute_action.blockSignals(True)
        self._mute_action.setEnabled(self._connected and CAPTURE_AVAILABLE)
        self._mute_action.blockSignals(False)

        if not enabled:
            self._stop_hands_free()
            if self._connected:
                self._set_status("Connected")
            self.chat.add_notice("Hands-free mode is off. The Speak button records a message.")
        elif self.vault.active_account() is None:
            self.chat.add_notice("Add an account first, then hands-free mode can listen.")
        elif not self._connected:
            self.chat.add_notice("Hands-free mode is on and starts as soon as you connect.")
        elif self._muted:
            self.chat.add_notice("Hands-free mode is on, but the microphone is muted.")
        else:
            self._start_hands_free()
        self._update_connection_actions()

    def _on_mute_toggled(self, muted: bool) -> None:
        self._muted = muted
        self._mute_action.blockSignals(True)
        self._mute_action.setChecked(muted)
        self._mute_action.blockSignals(False)
        if muted:
            self._cancel_recording()
            self._stop_hands_free()
            if self._connected:
                self._set_status("Microphone muted")
        elif self._hands_free_enabled and self._connected:
            self._start_hands_free()
        self._update_connection_actions()

    def _on_mute_action(self, muted: bool) -> None:
        """The menu entry mirrors the checkbox."""
        self.composer.mute.blockSignals(True)
        self.composer.mute.setChecked(muted)
        self.composer.mute.blockSignals(False)
        self._on_mute_toggled(muted)

    @property
    def _hands_free_enabled(self) -> bool:
        return self.composer.auto_speak.isChecked()

    def _start_hands_free(self) -> None:
        if self._hands_free or not CAPTURE_AVAILABLE or not self._connected:
            return
        capture = self._capture_instance()
        if not capture.start(self.vault.settings.input_device):
            return  # the reason was reported through ``failed``
        self._record_level = 0.0
        self._hands_free = True
        self._resume_pending = False
        self._hf_waiting = False
        self._hf_speech = 0
        self._hf_silence = 0
        self._prebuffer.clear()
        self._set_status("Listening…")
        self._update_connection_actions()

    def _stop_hands_free(self) -> None:
        if not self._hands_free:
            return
        self._hands_free = False
        self._resume_pending = False
        self._hf_waiting = False
        self._hf_speech = 0
        self._hf_silence = 0
        self._prebuffer.clear()
        self._cancel_recording()
        if self._capture is not None and self._capture.recording:
            self._capture.stop()
        self._update_connection_actions()

    def _begin_hands_free_utterance(self) -> None:
        """Somebody started talking: record exactly like the Speak button."""
        if self._recording or self._pending_capture:
            return
        if self._streaming:
            # Talked over Xiaozhi: interrupt the reply and listen instead.
            self._streaming = False
            self._resume_pending = False
            self._bridge.abort()
            self.chat.finish_reply()
            self._set_status("Listening…")
        self._hf_waiting = True
        self._hf_silence = 0
        # The words that woke the recording count as speech for the pause
        # detection, otherwise a short "yes" would never be sent.
        self._hf_speech = SPEECH_FRAMES
        buffered = list(self._prebuffer)
        self._prebuffer.clear()
        self.start_recording()
        if self._pending_capture or self._recording:
            # Frames from before the first loud one are part of the message.
            self._pending_frames = buffered + self._pending_frames

    def _end_hands_free_utterance(self) -> None:
        """Two seconds of quiet: send the message like a released button."""
        self._hf_waiting = True
        self._hf_speech = 0
        self._hf_silence = 0
        self.stop_recording(keep_capture=True)
        # Without a reply there is nothing that would start listening again.
        QTimer.singleShot(15000, self._resume_hands_free_timeout)

    def _resume_hands_free_timeout(self) -> None:
        if not self._hands_free or not self._hf_waiting or self._streaming:
            return
        self._resume_hands_free()

    def _resume_hands_free(self) -> None:
        """Listen again as soon as the reply has been played."""
        if not self._hands_free or not self._resume_pending:
            return
        if self._player is not None and self._player.busy:
            return
        self._resume_pending = False
        self._hf_waiting = False
        self._hf_speech = 0
        self._hf_silence = 0
        self._prebuffer.clear()
        self._record_level = 0.0
        if self._capture is not None and not self._capture.recording:
            # The device went away while Xiaozhi was talking; open it again.
            self._capture.start(self.vault.settings.input_device)
        self._set_status("Listening…")

    def _update_record_status(self) -> None:
        if not self._recording:
            return
        seconds = int(time.monotonic() - self._record_started_at)
        meter = _level_meter(self._record_level)
        self._set_status(f"Recording…  {seconds // 60}:{seconds % 60:02d}   {meter}")

    def _mic_disabled_reason(self) -> str:
        if self._hands_free:
            return "Hands-free mode is on: just speak, or untick Auto-speak."
        if not CAPTURE_AVAILABLE:
            return "Voice input needs Qt Multimedia, which is not installed."
        if not self._connected:
            return "Connect first to record a voice message."
        if self._streaming:
            return "Wait until Xiaozhi has finished the reply."
        return IDLE_TOOLTIP

    # -- audio --------------------------------------------------------------

    def _pump_audio(self) -> None:
        if self._player is None:
            return
        self._player.pump(self._bridge.audio_queue, PLAYBACK_SAMPLE_RATE)
        self._resume_hands_free()

    def _player_stop(self) -> None:
        self._audio_timer.stop()
        if self._player is not None:
            self._player.stop()

    def _on_audio_toggled(self, enabled: bool) -> None:
        if self.vault.settings.play_audio == enabled:
            return
        self.vault.settings.play_audio = enabled
        self._apply_settings()
        self._save_vault()

    def _apply_settings(self) -> None:
        enabled = self.vault.settings.play_audio and self._player is not None
        self._audio_action.setEnabled(self._player is not None)
        self._audio_action.setChecked(self.vault.settings.play_audio)
        if self._player is not None:
            self._player.set_enabled(self.vault.settings.play_audio)
        if not enabled:
            self._audio_timer.stop()
        elif self._connected:
            self._audio_timer.start()

    # -- accounts and settings ---------------------------------------------

    def _refresh_accounts_menu(self) -> None:
        self._accounts_menu.clear()
        self._source_actions.clear()
        active = self.vault.active_account_id
        if not self.vault.accounts:
            empty = QAction("No accounts configured", self)
            empty.setEnabled(False)
            self._accounts_menu.addAction(empty)
            return
        for account in self.vault.accounts:
            action = QAction(account.name or account.mac_address, self, checkable=True)
            action.setChecked(account.id == active)
            action.triggered.connect(
                lambda _checked=False, account_id=account.id: self._switch_account(account_id)
            )
            self._accounts_menu.addAction(action)
            self._source_actions[account.id] = action

    def _switch_account(self, account_id: str) -> None:
        if account_id == self.vault.active_account_id:
            return
        self.vault.set_active(account_id)
        self._save_vault()
        self._refresh_accounts_menu()
        account = self.vault.active_account()
        if account is not None:
            self._account_label.setText(account.name)
        if self._connected or self._bridge.running:
            self.connect_active()

    def manage_settings(self) -> None:
        dialog = SettingsAccountsDialog(self.vault, self.store, self)
        if dialog.exec() != QDialog.Accepted:
            return
        previous_default = self.vault.active_account_id
        previous_audio = self.vault.settings.play_audio
        previous_device = self.vault.settings.input_device
        self.vault = dialog.vault
        if not self._save_vault():
            return
        self._refresh_accounts_menu()
        self._apply_settings()
        if self._hands_free and self.vault.settings.input_device != previous_device:
            self._stop_hands_free()
            if not self._muted:
                self._start_hands_free()
        if (
            self.vault.active_account_id != previous_default
            or self.vault.settings.play_audio != previous_audio
        ) and (self._connected or self._bridge.running):
            self.connect_active()

    def open_setup_wizard(self) -> None:
        wizard = SetupWizard(self.store, self)
        if wizard.exec() != QDialog.Accepted:
            return
        account = wizard.account()
        if not account.is_valid():
            return
        self.vault.add_account(account)
        if not self._save_vault():
            return
        self._refresh_accounts_menu()
        self._account_label.setText(account.name)
        self.connect_active()

    def _save_vault(self) -> bool:
        try:
            self.store.save(self.vault)
        except StorageError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return False
        return True

    # -- automatic reconnect ------------------------------------------------

    def _schedule_reconnect(self) -> None:
        """Try to reconnect after a drop, unless the user asked to stay offline."""
        if self._closing or self._manual_disconnect or not self._ever_connected:
            return
        if not self.vault.settings.auto_reconnect:
            return
        if self.vault.active_account() is None:
            return
        delay = RECONNECT_DELAYS[min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
        self._reconnect_attempt += 1
        self._set_status(f"Disconnected — reconnecting in {delay} s…")
        logger.debug("Reconnecting in %s seconds (attempt %s)", delay, self._reconnect_attempt)
        self._reconnect_timer.start(delay * 1000)

    def _reconnect(self) -> None:
        if self._closing or self._manual_disconnect:
            return
        logger.debug("Reconnecting to %s", self._account_label.text())
        self.connect_active()

    # -- connection log -----------------------------------------------------

    def show_connection_log(self) -> None:
        """Show the log window, reusing it when it is already open."""
        if self._log_dialog is None:
            self._log_dialog = LogViewerDialog(self._log_buffer, self)
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    # -- help ---------------------------------------------------------------

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard shortcuts",
            "<b>Enter</b> — send the message<br>"
            "<b>Shift+Enter</b> — insert a new line<br>"
            "<b>Ctrl+R</b> — connect<br>"
            "<b>Ctrl+D</b> — disconnect<br>"
            "<b>Ctrl+.</b> — stop the current reply<br>"
            "<b>Ctrl+Shift+Space</b> — record a voice message, again to send<br>"
            "<b>Ctrl+M</b> — mute the microphone<br>"
            "<b>Ctrl+L</b> — clear the chat log<br>"
            "<b>Ctrl+Shift+L</b> — connection log<br>"
            "<b>Ctrl+,</b> — settings and accounts<br>"
            "<b>Ctrl+Q</b> — quit",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Xiaozhi Talk",
            f"<h3>Xiaozhi Talk {__version__}</h3>"
            "<p>A desktop chat client for Xiaozhi servers, built with PySide6 and "
            "the <i>xiaozhi-sdk</i>.</p>"
            "<p>Credentials are stored in: "
            f"{self.store.backend_label}</p>",
        )

    # -- shutdown -----------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        self._closing = True
        self._reconnect_timer.stop()
        self._qt_settings.setValue("window/geometry", self.saveGeometry())
        self._stop_hands_free()
        self._cancel_recording()
        self._bridge.stop()
        logging.getLogger().removeHandler(self._log_buffer)
        if self._log_dialog is not None:
            self._log_dialog.close()
        if self._player is not None:
            self._player.stop()
        super().closeEvent(event)
