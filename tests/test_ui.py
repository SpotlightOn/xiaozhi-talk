"""Offscreen smoke tests for the widgets."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QListWidget

from xiaozhi_talk.audio_input import MULTIMEDIA_AVAILABLE as CAPTURE_AVAILABLE
from xiaozhi_talk.ui.accounts_dialog import SettingsAccountsDialog
from xiaozhi_talk.ui.chat_view import ChatView
from xiaozhi_talk.ui.composer import Composer
from xiaozhi_talk.ui.main_window import MainWindow
from xiaozhi_talk.ui.setup_wizard import SetupWizard

# ---------------------------------------------------------------------------
# chat view
# ---------------------------------------------------------------------------


def test_reply_is_streamed_into_one_bubble(qapp) -> None:
    view = ChatView()
    view.add_user_message("Hello")
    view.start_reply()
    view.append_reply("Hello there.")
    view.append_reply("How can I help?")
    view.finish_reply()

    assert view.messages() == [
        ("user", "Hello"),
        ("assistant", "Hello there. How can I help?"),
    ]


def test_empty_reply_is_dropped(qapp) -> None:
    view = ChatView()
    view.start_reply()
    view.finish_reply()

    assert view.messages() == []


def test_a_second_reply_gets_its_own_bubble(qapp) -> None:
    view = ChatView()
    view.start_reply()
    view.append_reply("First")
    view.finish_reply()
    view.start_reply()
    view.append_reply("Second")
    view.finish_reply()

    assert view.messages() == [("assistant", "First"), ("assistant", "Second")]


def test_long_notice_wraps_instead_of_being_cut_off(qapp) -> None:
    view = ChatView()
    view.resize(900, 500)
    view.show()

    label = view.add_notice(
        "Could not connect to wss://api.tenclass.net/xiaozhi/v1/. Check the MAC "
        "address, the URL and your network connection.",
        error=True,
    )

    line_height = label.fontMetrics().height()
    assert label.width() == min(720, view.viewport().width() - 40)
    # More than one line means the text wrapped instead of being clipped.
    assert label.sizeHint().height() > line_height + 2
    assert label.objectName() == "ChatError"
    assert label.text().endswith("network connection.")


def test_notice_width_follows_the_window(qapp) -> None:
    view = ChatView()
    view.resize(900, 500)
    view.show()
    label = view.add_notice("Hello")

    view.resize(600, 500)
    qapp.processEvents()

    assert label.width() < 600
    assert label.width() > 300


def test_clear_removes_everything(qapp) -> None:
    view = ChatView()
    view.add_user_message("Hello")
    view.clear_messages()

    assert view.messages() == []


# ---------------------------------------------------------------------------
# composer
# ---------------------------------------------------------------------------


def test_enter_sends_and_clears(qapp) -> None:
    composer = Composer()
    composer.show()
    received: list[str] = []
    composer.sendRequested.connect(received.append)

    composer.editor.setPlainText("Hello Xiaozhi")
    QTest.keyClick(composer.editor, Qt.Key_Return)

    assert received == ["Hello Xiaozhi"]
    assert composer.text() == ""


def test_shift_enter_inserts_a_new_line(qapp) -> None:
    composer = Composer()
    composer.show()
    received: list[str] = []
    composer.sendRequested.connect(received.append)

    composer.editor.setPlainText("First line")
    composer.editor.moveCursor(QTextCursor.End)
    QTest.keyClick(composer.editor, Qt.Key_Return, Qt.ShiftModifier)

    assert received == []
    assert composer.text() == "First line\n"


def test_empty_input_is_not_sent(qapp) -> None:
    composer = Composer()
    received: list[str] = []
    composer.sendRequested.connect(received.append)

    composer.editor.setPlainText("   ")
    composer.submit()

    assert received == []


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------


def test_bridge_events_reach_the_chat_view(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window.show()

    window._bridge.replyStarted.emit()
    window._bridge.replyChunk.emit("Hello")
    window._bridge.replyChunk.emit("friend.")
    window._bridge.replyFinished.emit()
    qapp.processEvents()

    assert ("assistant", "Hello friend.") in window.chat.messages()


def test_sending_without_connection_keeps_the_text(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window.composer.editor.setPlainText("Hello")
    window.composer.submit()

    assert window.chat.messages() == []
    assert window.composer.text() == "Hello"


def test_user_message_is_shown_before_it_is_sent(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window._connected = True

    window.send_message("Hi")

    assert ("user", "Hi") in window.chat.messages()


def test_shortcut_names_are_english(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    shortcut = window._connect_action.shortcut().toString(QKeySequence.NativeText)

    assert shortcut == "Ctrl+R"


def test_connect_and_disconnect_are_mutually_exclusive(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    assert window._connect_action.isEnabled()
    assert not window._disconnect_action.isEnabled()

    window._on_connected()
    assert not window._connect_action.isEnabled()
    assert window._disconnect_action.isEnabled()

    window._on_disconnected("The server closed the connection.")
    assert window._connect_action.isEnabled()
    assert not window._disconnect_action.isEnabled()

    window._closing = True
    window._reconnect_timer.stop()


def test_failed_connection_offers_connect_again(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window._connecting = True
    window._update_connection_actions()

    window._on_error("Could not reach ws://127.0.0.1:1: connection refused")

    assert window._connect_action.isEnabled()
    assert not window._disconnect_action.isEnabled()
    assert window._status_label.text() == "Not connected"


def test_stop_is_only_available_while_a_reply_runs(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    assert not window._stop_action.isEnabled()

    window._on_reply_started()
    assert window._stop_action.isEnabled()

    window._on_reply_finished()
    assert not window._stop_action.isEnabled()


def test_dropped_connection_schedules_a_reconnect(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window._on_connected()
    window._on_disconnected("The server closed the connection.")

    assert window._reconnect_timer.isActive()
    assert "reconnecting" in window._status_label.text()

    window._closing = True
    window._reconnect_timer.stop()


def test_manual_disconnect_does_not_reconnect(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    window._on_connected()
    window.disconnect_session()
    window._on_disconnected("Disconnected.")

    assert not window._reconnect_timer.isActive()


def test_reconnect_can_be_switched_off(qapp, store, vault) -> None:
    vault.settings.auto_reconnect = False
    window = MainWindow(store, vault)
    window._on_connected()
    window._on_disconnected("The server closed the connection.")

    assert not window._reconnect_timer.isActive()


def test_connection_log_window_is_reused(qapp, store, vault) -> None:
    window = MainWindow(store, vault)

    window.show_connection_log()
    first = window._log_dialog
    window.show_connection_log()

    assert first is not None
    assert window._log_dialog is first
    assert first.isVisible()
    first.close()


# ---------------------------------------------------------------------------
# voice input
# ---------------------------------------------------------------------------


class FakeCapture(QObject):
    """Stand-in for :class:`MicrophoneCapture`; no audio device is opened."""

    frameReady = Signal(bytes)
    level = Signal(float)
    started = Signal()
    stopped = Signal()
    failed = Signal(str)

    def __init__(self, peak: float = 0.2) -> None:
        super().__init__()
        self.recording = False
        self.device: str | None = None
        self.peak = peak

    def start(self, device_id: str = "") -> bool:
        self.device = device_id
        self.recording = True
        self.started.emit()
        return True

    def stop(self) -> None:
        self.recording = False
        self.stopped.emit()


def _install_capture(window: MainWindow, capture: FakeCapture) -> None:
    """Wire the fake the way the window wires a real capture."""
    window._capture = capture  # type: ignore[assignment]
    capture.frameReady.connect(window._on_capture_frame)
    capture.level.connect(window._on_capture_level)
    capture.failed.connect(window._on_capture_failed)


def _connected_window(store, vault) -> MainWindow:
    window = MainWindow(store, vault)
    window._on_connected()
    return window


def test_the_microphone_needs_a_connection(qapp, store, vault) -> None:
    window = MainWindow(store, vault)

    assert not window.composer.mic_button.isEnabled()
    assert "Connect first" in window.composer.mic_button.toolTip()


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_the_microphone_is_blocked_while_a_reply_runs(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    assert window.composer.mic_button.isEnabled()

    window._on_reply_started()

    assert not window.composer.mic_button.isEnabled()
    assert "finished the reply" in window.composer.mic_button.toolTip()

    window._on_reply_finished()

    assert window.composer.mic_button.isEnabled()


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_the_first_click_starts_recording(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)

    window.toggle_recording()

    assert capture.recording is True
    assert window._recording is True
    assert window.composer.mic_button.text() == "Stop"
    assert "Recording" in window._status_label.text()


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_the_second_click_sends_the_recording(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.toggle_recording()
    window._bridge.speechReady.emit(True)

    window.toggle_recording()

    assert capture.recording is False
    assert window.composer.mic_button.text() == "Speak"
    assert window._status_label.text() == "Connected"


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_the_chosen_microphone_is_used(qapp, store, vault) -> None:
    vault.settings.input_device = "usb-headset"
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)

    window.toggle_recording()

    assert capture.device == "usb-headset"


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_frames_wait_until_the_server_listens(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    received: list[bytes] = []
    window._bridge.push_audio = received.append  # type: ignore[method-assign]

    window.toggle_recording()
    capture.frameReady.emit(b"first")
    # The microphone is open but the server is not listening yet.
    assert window._pending_frames == [b"first"]
    assert received == []

    window._bridge.speechReady.emit(True)
    assert received == [b"first"]

    capture.frameReady.emit(b"second")
    assert received == [b"first", b"second"]
    assert window._pending_frames == []


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_a_refused_recording_resets_the_button(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.toggle_recording()

    window._bridge.speechReady.emit(False)

    assert capture.recording is False
    assert window._recording is False
    assert window.composer.mic_button.text() == "Speak"
    assert window._status_label.text() == "Connected"


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_a_microphone_failure_is_reported_and_resets_the_button(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.toggle_recording()

    capture.failed.emit("No microphone was found. Connect one and try again.")

    assert window.composer.mic_button.text() == "Speak"
    assert not window._recording
    texts = [label.text() for label in window.chat.findChildren(QLabel)]
    assert any("No microphone was found" in text for text in texts)


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_a_reply_stops_the_recording(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    requested: list[bool] = []
    window._bridge.end_speech = lambda: requested.append(True)  # type: ignore[method-assign]
    window.toggle_recording()
    window._bridge.speechReady.emit(True)

    window._on_reply_started()

    assert capture.recording is False
    assert window._recording is False
    assert window.composer.mic_button.text() == "Speak"
    assert requested == [True]


def test_a_silent_recording_is_reported(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture(peak=0.0)
    _install_capture(window, capture)
    window.toggle_recording()
    window._bridge.speechReady.emit(True)

    window.toggle_recording()

    texts = [label.text() for label in window.chat.findChildren(QLabel)]
    assert any("almost silent" in text for text in texts)


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_the_level_meter_follows_the_microphone(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.toggle_recording()

    capture.level.emit(0.5)
    window._update_record_status()

    assert "▮" in window._status_label.text()
    assert "Recording" in window._status_label.text()


def test_a_dropped_connection_stops_the_recording(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.toggle_recording()
    window._bridge.speechReady.emit(True)

    window._on_disconnected("The server closed the connection.")

    assert capture.recording is False
    assert not window._recording
    window._closing = True
    window._reconnect_timer.stop()


def test_the_composer_has_the_hands_free_row(qapp, store, vault) -> None:
    window = MainWindow(store, vault)

    assert window.composer.auto_speak.text() == "Auto-speak"
    assert window.composer.mute.text() == "Mute"
    assert window.composer.auto_speak.isChecked() is False
    assert window.composer.mute.isChecked() is False


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_auto_speak_listens_without_the_button(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.composer.auto_speak.setChecked(True)

    assert capture.recording is True
    assert window._hands_free is True
    assert "Listening" in window._status_label.text()
    assert window.composer.mic_button.isEnabled() is False


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_auto_speak_is_remembered(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    _install_capture(window, FakeCapture())
    window._bridge.start_listening = lambda: None  # type: ignore[method-assign]

    window.composer.auto_speak.setChecked(True)

    assert window.vault.settings.auto_speak is True
    # Speaking the reply aloud belongs to hands-free mode.
    assert window.vault.settings.play_audio is True
    assert store.load().settings.auto_speak is True


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_mute_stops_the_microphone(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.composer.auto_speak.setChecked(True)

    window.composer.mute.setChecked(True)

    assert capture.recording is False
    assert window._hands_free is False
    assert window._status_label.text() == "Microphone muted"


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_unmuting_listens_again(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.composer.auto_speak.setChecked(True)
    window.composer.mute.setChecked(True)

    window.composer.mute.setChecked(False)

    assert capture.recording is True
    assert "Listening" in window._status_label.text()


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_hands_free_listens_again_after_a_reply(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    _install_capture(window, FakeCapture())
    window.composer.auto_speak.setChecked(True)
    window._hf_waiting = True

    window._on_reply_started()
    window._on_reply_finished()
    window._pump_audio()

    assert window._hf_waiting is False
    assert "Listening" in window._status_label.text()


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_hands_free_blocks_the_speak_button(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    window.composer.auto_speak.setChecked(True)

    window.toggle_recording()

    assert window._recording is False
    texts = [label.text() for label in window.chat.findChildren(QLabel)]
    assert any("Hands-free mode is on" in text for text in texts)


@pytest.mark.skipif(not CAPTURE_AVAILABLE, reason="Qt Multimedia is not installed")
def test_hands_free_records_when_somebody_talks(qapp, store, vault) -> None:
    window = _connected_window(store, vault)
    capture = FakeCapture()
    _install_capture(window, capture)
    calls: list[str] = []
    window._bridge.start_speech = lambda: calls.append("start")  # type: ignore[method-assign]
    window._bridge.stop_speech = lambda: calls.append("stop")  # type: ignore[method-assign]
    window.composer.auto_speak.setChecked(True)

    loud = b"\xb8\x0b" * 1440  # 3000, clearly speech
    quiet = b"\x00\x00" * 1440
    for _ in range(3):
        capture.frameReady.emit(loud)
    assert calls == ["start"]
    assert window._pending_capture is True

    window._bridge.speechReady.emit(True)  # the server listens now
    for _ in range(34):
        capture.frameReady.emit(quiet)

    assert calls == ["start", "stop"]
    assert window._recording is False
    # The microphone stays open for the next question.
    assert capture.recording is True


def test_the_voice_shortcut_is_english(qapp, store, vault) -> None:
    window = MainWindow(store, vault)
    shortcut = window._voice_action.shortcut().toString(QKeySequence.NativeText)

    assert shortcut == "Ctrl+Shift+Space"


# ---------------------------------------------------------------------------
# dialogs
# ---------------------------------------------------------------------------


def test_accounts_dialog_works_on_a_copy(qapp, store, vault) -> None:
    dialog = SettingsAccountsDialog(vault, store)

    assert dialog.vault is not vault
    assert dialog.vault.accounts[0].name == vault.accounts[0].name
    accounts = dialog.findChild(QListWidget)
    assert accounts is not None
    assert accounts.count() == 1


def test_the_settings_page_lists_the_microphones(qapp, store, vault) -> None:
    dialog = SettingsAccountsDialog(vault, store)
    combo = dialog.findChild(QComboBox)

    assert combo is not None
    assert combo.itemText(0) == "System default"
    assert combo.itemData(0) == ""


def test_the_microphone_choice_is_saved(qapp, store, vault) -> None:
    dialog = SettingsAccountsDialog(vault, store)
    combo = dialog.findChild(QComboBox)
    assert combo is not None

    last = combo.count() - 1
    combo.setCurrentIndex(last)
    dialog._on_accept()

    assert dialog.vault.settings.input_device == combo.itemData(last)


def test_an_unplugged_microphone_falls_back_to_the_default(qapp, store, vault) -> None:
    vault.settings.input_device = "headset-that-is-gone"

    dialog = SettingsAccountsDialog(vault, store)

    combo = dialog.findChild(QComboBox)
    assert combo is not None
    assert combo.currentIndex() == 0


def test_wizard_buttons_are_english(qapp, store) -> None:
    from PySide6.QtWidgets import QWizard

    wizard = SetupWizard(store)
    assert wizard.buttonText(QWizard.NextButton) == "Next >"
    assert wizard.buttonText(QWizard.CancelButton) == "Cancel"
    assert wizard.buttonText(QWizard.FinishButton) == "Start chatting"


def test_setup_wizard_requires_a_mac_address(qapp, store) -> None:
    wizard = SetupWizard(store)
    assert wizard.pageIds() == [0, 1, 2]
    assert wizard.account().is_valid() is False

    wizard.accept()
    assert wizard.result() != QDialog.Accepted
