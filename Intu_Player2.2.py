import sys
import os
import json
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QSlider, QListWidget, QStyle,
    QSizePolicy, QDialog, QAbstractItemView, QComboBox, QListWidgetItem
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from PyQt6.QtCore import Qt, QUrl, QDir, QTime, QByteArray
from PyQt6.QtGui import QPixmap, QResizeEvent, QCloseEvent
from pypinyin import lazy_pinyin

SETTINGS_FILE = "settings.json"

DARK_STYLE_SHEET = """
QMainWindow, QDialog, QWidget {background-color: #2b2b2b; color: #f0f0f0;}
QPushButton {background-color: #505050; color: #f0f0f0; border: 1px solid #606060; padding: 5px 10px; border-radius: 4px;}
QPushButton:hover {background-color: #606060;}
QPushButton:pressed {background-color: #707070;}
QLabel {color: #f0f0f0;}
QSlider::groove:horizontal {border: 1px solid #505050; height: 8px; background: #3a3a3a; margin: 2px 0; border-radius: 4px;}
QSlider::handle:horizontal {background: #0078d4; border: 1px solid #0078d4; width: 14px; margin: -3px 0; border-radius: 7px;}
QSlider::add-page:horizontal {background: #505050;}
QSlider::sub-page:horizontal {background: #0078d4;}
QListWidget {background-color: #3c3c3c; color: #f0f0f0; border: 1px solid #505050; selection-background-color: #0078d4; selection-color: #ffffff;}
QListWidget::item:hover {background-color: #555555;}
"""

# =================== 进度条点击跳转：1 ===================
class ClickableSlider(QSlider):
    """一个可以点击任意位置跳转的 QSlider"""
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 计算鼠标位置对应的值
            ratio = event.position().x() / self.width()
            value = self.minimum() + ratio * (self.maximum() - self.minimum())
            self.setValue(int(value))
            # 立即发送 sliderMoved 信号，相当于用户拖动后释放
            # 这里的 set_position 方法已经连接了 sliderMoved，因此直接设置值即可
            # 如果需要模拟用户拖动结束，可以手动发送 signal
            self.sliderMoved.emit(int(value))
            event.accept()
            return
        super().mousePressEvent(event)

# =================== 图片显示标签 ===================
class ImageDisplayLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self.setStyleSheet("background-color: #3c3c3c; border: 1px solid #505050;")
        self._original_pixmap = QPixmap()

    def setOriginalPixmap(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        if not self._original_pixmap.isNull():
            super().setText("")
            self._scale_pixmap()
        else:
            super().setPixmap(QPixmap())
            super().setText("无图片")

    def _scale_pixmap(self):
        if self._original_pixmap.isNull(): return
        label_size = self.size()
        if label_size.width() <= 0 or label_size.height() <= 0: return
        scaled_pixmap = self._original_pixmap.scaled(
            label_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        super().setPixmap(scaled_pixmap)

    def resizeEvent(self, event: QResizeEvent):
        if event.oldSize() != event.size():
            self._scale_pixmap()
        super().resizeEvent(event)

# =================== 播放列表 ===================
class PlaylistDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("播放列表")
        self.setGeometry(200, 200, 300, 400)
        self.parent = parent
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.playlist_widget = QListWidget()
        self.playlist_widget.itemDoubleClicked.connect(self.item_double_clicked)
        self.playlist_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.playlist_widget)
        
        # ********************** 播放列表按钮布局修改 **********************
        button_layout = QHBoxLayout()
        
        # 添加刷新按钮
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_list)
        
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        
        button_layout.addWidget(self.refresh_button) # 放在关闭左侧
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        # ***************************************************************

    def item_double_clicked(self, item):
        if self.parent:
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if file_path:
                self.parent.playlist_file_double_clicked(file_path)
                self.close()

    # ********************** 添加刷新功能 **********************
    def refresh_list(self):
        """通知主播放器重新加载当前目录的播放列表"""
        if self.parent:
            self.parent.reload_current_directory_playlist()
    # *******************************************************

# =================== 主播放器 ===================
class AudioPlayer(QMainWindow):
    AUDIO_EXTENSIONS = ('.mp3', '.wav', '.flac', '.ogg', '.m4a')

    def __init__(self):
        super().__init__()
        self.setWindowTitle("音图播放器")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # === 加载设置 ===
        self.last_dir = ""
        self.last_device_desc = ""
        self.playlist_memory = []
        self.current_file_path = None
        self.window_geometry = None 
        
        self.load_settings()

        # 恢复窗口位置和大小
        if self.window_geometry:
            self.restoreGeometry(self.window_geometry)
        else:
            self.setGeometry(200, 200, 800, 500)

        self.player = QMediaPlayer()
        # 按照描述升序排列声卡
        self.audio_devices = sorted(QMediaDevices.audioOutputs(), key=lambda d: d.description()) 

        # 默认声卡
        if self.audio_devices:
            default_device = next((d for d in self.audio_devices if d.description() == self.last_device_desc),
                                  self.audio_devices[0])
            self.audio_output = QAudioOutput(default_device)
        else:
            self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        # 播放列表
        self.playlist = []
        self.current_index = -1

        # 主布局
        main_container = QWidget()
        full_layout = QVBoxLayout(main_container)
        full_layout.setContentsMargins(10, 10, 10, 10)
        full_layout.setSpacing(10)
        self.setCentralWidget(main_container)

        # 图片显示
        self.image_label = ImageDisplayLabel("无图片")
        full_layout.addWidget(self.image_label, 20)

        # 控制条容器（进度条在上方）
        control_container = QVBoxLayout()

        # 播放进度条
        progress_layout = QHBoxLayout()
        self.position_label = QLabel("00:00")
        self.duration_label = QLabel("00:00")

        self.position_slider = ClickableSlider(Qt.Orientation.Horizontal) 

        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.set_position)
        progress_layout.addWidget(self.position_label)
        progress_layout.addWidget(self.position_slider, 1)
        progress_layout.addWidget(self.duration_label)
        control_container.addLayout(progress_layout)

        # 播放控制按钮
        self.open_button = QPushButton("打开文件")
        self.open_button.clicked.connect(self.open_file)
        self.previous_button = QPushButton()
        self.previous_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.previous_button.clicked.connect(self.play_previous)
        self.play_button = QPushButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.clicked.connect(self.play_pause)
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop)
        self.next_button = QPushButton()
        self.next_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.next_button.clicked.connect(self.play_next)
        self.playlist_button = QPushButton("列表")
        self.playlist_button.clicked.connect(self.show_playlist)

        # 为声卡按钮和下拉框做准备
        self.device_button = QPushButton("声卡")
        self.device_button.clicked.connect(self.toggle_device_combo) 

        # QComboBox 用于显示和选择声卡
        self.device_combo = QComboBox()
        self.device_combo.addItems([d.description() for d in self.audio_devices])
        if self.last_device_desc in [d.description() for d in self.audio_devices]:
            self.device_combo.setCurrentText(self.last_device_desc)

        # 选中声卡后自动收回列表（隐藏 QComboBox）
        self.device_combo.currentIndexChanged.connect(self.change_audio_device)
        self.device_combo.currentIndexChanged.connect(self.device_combo.hide) 

        self.device_combo.setMinimumWidth(self.device_button.width()) # 尝试保持下拉框宽度

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(lambda val: self.audio_output.setVolume(val/100))

        control_layout = QHBoxLayout()
        for w in [self.open_button, self.previous_button, self.play_button, self.stop_button,
                  self.next_button, self.playlist_button, self.device_button, self.volume_slider]:
            control_layout.addWidget(w)

        control_container.addLayout(control_layout)
        full_layout.addLayout(control_container)

        self.device_combo.hide() # 确保隐藏

        # 连接播放器信号
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.handle_media_status_change)
        self.player.playbackStateChanged.connect(self.update_play_button_icon)

        self.playlist_dialog = PlaylistDialog(self)

        # 自动加载上次播放列表
        if self.playlist_memory:
            self.playlist = self.playlist_memory
            self.playlist_dialog.playlist_widget.clear()
            for f in self.playlist:
                item = QListWidgetItem(os.path.basename(f))
                item.setData(Qt.ItemDataRole.UserRole, f)
                self.playlist_dialog.playlist_widget.addItem(item)
            if self.current_file_path in self.playlist:
                self.current_index = self.playlist.index(self.current_file_path)
                self.playlist_file_double_clicked(self.current_file_path)
                self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)

    def closeEvent(self, event: QCloseEvent):
        self.save_settings()
        super().closeEvent(event)

    # ================== 核心方法 ==================
    def format_time(self, ms):
        t = QTime(0, 0, 0).addMSecs(ms)
        return t.toString("mm:ss") if ms < 3600000 else t.toString("hh:mm:ss")

    def set_position(self, pos):
        self.player.setPosition(pos)

    def update_position(self, pos):
        # 避免在用户拖动时更新滑块位置
        if not self.position_slider.isSliderDown(): 
            self.position_slider.setValue(pos)
        self.position_label.setText(self.format_time(pos))

    def update_duration(self, dur):
        self.position_slider.setRange(0, dur)
        self.duration_label.setText(self.format_time(dur))

    def toggle_device_combo(self):
        # 强制弹出下拉列表
        self.device_combo.showPopup() 

    def open_file(self):
        filter_str = f"音频文件 ({' '.join(['*' + ext for ext in self.AUDIO_EXTENSIONS])})"
        file_path, _ = QFileDialog.getOpenFileName(self, "选择音频文件", self.last_dir, filter_str)
        if not file_path: return
        self.last_dir = os.path.dirname(file_path)
        normalized = os.path.abspath(file_path).replace(os.sep, '/')
        self.load_directory_to_playlist(os.path.dirname(normalized))
        self.play_file(normalized)
        try:
            self.current_index = self.playlist.index(normalized)
            self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)
        except ValueError:
            self.current_index = -1
        self.save_settings()

    # ********************** 添加刷新当前目录播放列表的方法 **********************
    def reload_current_directory_playlist(self):
        """重新加载当前音频文件所在的目录作为播放列表"""
        current_dir = None
        if self.current_file_path:
            # 如果当前有文件正在播放，则使用其所在目录
            current_dir = os.path.dirname(self.current_file_path)
        elif self.last_dir:
            # 否则使用上次打开文件的目录
            current_dir = self.last_dir

        if current_dir and os.path.isdir(current_dir):
            # 保存当前播放状态，以便重新加载后恢复
            was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            current_pos = self.player.position() if was_playing else 0
            
            # 重新加载目录
            self.load_directory_to_playlist(current_dir)
            
            # 尝试恢复播放的文件和位置
            if self.current_file_path in self.playlist:
                self.current_index = self.playlist.index(self.current_file_path)
                self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)

                # 重新设置播放源（PyQt6 Media Player 在更换播放列表后需要重新设置 Source 才能跳转）
                self.player.setSource(QUrl.fromLocalFile(self.current_file_path))
                self.player.setPosition(current_pos)
                
                if was_playing:
                    self.player.play()
            else:
                # 如果当前播放的文件不在新列表里，则停止播放，并重置索引
                self.stop()
                self.current_index = -1
                self.current_file_path = None
        
        self.save_settings()
    # *************************************************************************

    def load_directory_to_playlist(self, directory):
        self.playlist.clear()
        self.playlist_dialog.playlist_widget.clear()
        dir_obj = QDir(directory)
        name_filters = [f"*{ext}" for ext in self.AUDIO_EXTENSIONS]
        # 使用 QDir.Filter.Files 确保只获取文件
        files = [f.absoluteFilePath().replace(os.sep, '/') for f in dir_obj.entryInfoList(name_filters, QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)]
        files.sort(key=lambda path: ''.join(lazy_pinyin(os.path.basename(path))))
        self.playlist = files
        self.playlist_memory = files # 同时更新设置内存
        
        for f in self.playlist:
            item = QListWidgetItem(os.path.basename(f))
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.playlist_dialog.playlist_widget.addItem(item)
            
    def play_file(self, file_path):
        self.current_file_path = file_path
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.load_image(file_path)
        self.player.play()
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.save_settings()

    def playlist_file_double_clicked(self, file_path):
        try:
            index = self.playlist.index(file_path)
            if 0 <= index < len(self.playlist):
                self.current_index = index
                self.play_file(self.playlist[index])
                self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)
        except ValueError:
            pass

    def play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        else:
            self.player.play()
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))

    def stop(self):
        self.player.stop()
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def play_previous(self):
        if not self.playlist: return
        self.current_index = (self.current_index - 1) % len(self.playlist)
        self.play_file(self.playlist[self.current_index])
        self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)

    def play_next(self):
        if not self.playlist: return
        self.current_index = (self.current_index + 1) % len(self.playlist)
        self.play_file(self.playlist[self.current_index])
        self.playlist_dialog.playlist_widget.setCurrentRow(self.current_index)

    def show_playlist(self):
        self.playlist_dialog.show()

    def handle_media_status_change(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()

    def update_play_button_icon(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))

    def change_audio_device(self, index):
        if 0 <= index < len(self.audio_devices):
            device = self.audio_devices[index]
            self.last_device_desc = device.description()
            new_output = QAudioOutput(device)
            new_output.setVolume(self.audio_output.volume())
            self.audio_output = new_output
            self.player.setAudioOutput(self.audio_output)
            if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState and self.current_file_path:
                pos = self.player.position()
                self.player.setSource(QUrl.fromLocalFile(self.current_file_path))
                self.player.setPosition(pos)
                self.player.play()
            self.save_settings()

    def load_image(self, audio_path):
        base_path = os.path.splitext(audio_path)[0]
        self.current_image_path = None
        for ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif']:
            image_path = base_path + ext
            if os.path.exists(image_path):
                self.current_image_path = image_path
                break
        if self.current_image_path:
            pixmap = QPixmap(self.current_image_path)
            if pixmap.isNull():
                self.image_label.setOriginalPixmap(QPixmap())
            else:
                self.image_label.setOriginalPixmap(pixmap)
        else:
            self.image_label.setOriginalPixmap(QPixmap())

# ================== 保存/加载设置 ==================
    def save_settings(self):
        data = {
            "last_dir": self.last_dir,
            "last_device_desc": self.last_device_desc,
            "playlist_memory": self.playlist_memory,
            "current_file_path": self.current_file_path,
            "window_geometry": self.saveGeometry().toBase64().data().decode('utf-8')
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_dir = data.get("last_dir", "")
                    self.last_device_desc = data.get("last_device_desc", "")
                    self.playlist_memory = data.get("playlist_memory", [])
                    self.current_file_path = data.get("current_file_path", None)
                    geometry_base64 = data.get("window_geometry")
                    if geometry_base64:
                        self.window_geometry = QByteArray.fromBase64(geometry_base64.encode('utf-8'))
            except json.JSONDecodeError:
                pass


# =================== 运行程序 ===================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE_SHEET)
    player = AudioPlayer()
    player.show()
    sys.exit(app.exec())
