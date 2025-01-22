import base64
import json
import os
import threading
from typing import Optional
from typing import Tuple

import requests
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QSize, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt5.QtGui import QPixmap, QImage, QFontDatabase, QColor, QPainter
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget, QSizePolicy, QFrame
from loguru import logger
from qfluentwidgets import isDarkTheme, ImageLabel

from .ClassWidgets.base import PluginBase

# 组件元数据
WIDGET_CODE = 'lx-music-lyrics.ui'
WIDGET_NAME = 'LX-music-Lyrics'
WIDGET_WIDTH = 340

# 服务配置
SSE_URL = 'http://127.0.0.1:23330/subscribe-player-status'
DEFAULT_LYRIC = '等待音乐软件传输数据...'


class UpdateSignal(QObject):
    """用于跨线程更新的信号类"""
    update_signal = pyqtSignal(dict, str)


class MusicData:
    """音乐数据存储结构"""

    def __init__(self):
        self.lyrics_text = ""
        self.song_name = ""
        self.artist = ""
        self.cover_url = ""
        self.duration = 0.0
        self.progress = 0.0


# 全局数据实例
music_data = MusicData()
update_signal = UpdateSignal()


class SSEClient:
    """SSE 客户端实现"""

    def __init__(self, url: str):
        self.url = url
        self.running = False
        self.session = requests.Session()

    @staticmethod
    def _parse_event_data(event_data: str) -> Tuple[str, str]:
        """解析SSE事件数据"""
        event_type, data = "", ""
        for line in event_data.split('\n'):
            line = line.strip()
            if line.startswith('event:'):
                event_type = line[6:].strip()
            elif line.startswith('data:'):
                data = line[5:].strip()
                try:  # 尝试解析JSON数据
                    data = json.loads(data) if data.startswith(('"', '{')) else data
                except json.JSONDecodeError:
                    pass
        return event_type, data

    def start(self):
        """启动SSE长连接"""
        self.running = True
        try:
            response = self.session.get(
                self.url,
                headers={'Accept': 'text/event-stream', 'Cache-Control': 'no-cache'},
                params={'filter': 'lyricLineAllText,name,singer,picUrl,duration,progress'},
                stream=True
            )
            response.encoding = 'utf-8'
            response.raise_for_status()

            event_buffer = []
            for line in response.iter_lines(decode_unicode=True):
                if not self.running:
                    break

                if line:
                    event_buffer.append(line)
                elif event_buffer:
                    event_type, data = self._parse_event_data('\n'.join(event_buffer))
                    self._update_music_data(event_type, data)
                    event_buffer = []

        except requests.RequestException as e:
            logger.error(f"SSE连接错误: {str(e)}")
            if self.running:
                threading.Timer(5.0, self.start).start()
        except Exception as e:
            logger.error(f"未预期错误: {str(e)}")
        finally:
            if not self.running:
                self.stop()

    @staticmethod
    def _update_music_data(event_type: str, data: str):
        """更新音乐数据到全局状态"""
        try:
            if event_type == 'lyricLineAllText':
                music_data.lyrics_text = data
            elif event_type == 'name':
                music_data.song_name = data
            elif event_type == 'singer':
                music_data.artist = data
            elif event_type == 'picUrl':
                music_data.cover_url = data
            elif event_type == 'duration':
                music_data.duration = max(float(data), 0.0) if data else 0.0
            elif event_type == 'progress':
                music_data.progress = max(float(data), 0.0) if data else 0.0

            update_signal.update_signal.emit({
                'lyrics': music_data.lyrics_text,
                'title': music_data.song_name,
                'artist': music_data.artist,
                'cover_url': music_data.cover_url,
                'duration': music_data.duration,
                'progress': music_data.progress
            }, WIDGET_NAME)
        except ValueError as e:
            logger.warning(f"数据解析失败: {str(e)}")

    def stop(self):
        """停止SSE连接"""
        self.running = False
        self.session.close()


class ProgressBar(QWidget):
    """自定义进度条组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress = 0.0
        self._duration = 1.0
        self._animated_progress = 0.0  # 用于动画的进度值
        self.setFixedHeight(3)
        self._bg_color = QColor(100, 100, 100, 50)
        self._fg_color = QColor(255, 255, 255, 200)

        # 创建动画对象
        self._animation = QPropertyAnimation(self, b"animated_progress")
        self._animation.setEasingCurve(QEasingCurve.Linear)
        self._animation.setDuration(100)  # 动画时间

    @pyqtProperty(float)
    def animated_progress(self):
        return self._animated_progress

    @animated_progress.setter
    def animated_progress(self, value):
        self._animated_progress = value
        self.update()

    def update_progress(self, progress: float, duration: float):
        # 立即停止当前动画
        self._animation.stop()

        # 设置绝对起止值（无需计算比例）
        self._animation.setStartValue(self._animated_progress)
        self._animation.setEndValue(progress)

        # 更新持续时间并启动动画
        self._duration = max(duration, 0.1)
        self._animation.start()

    def update_colors(self, bg: QColor, fg: QColor):
        self._bg_color = bg
        self._fg_color = fg
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 绘制背景
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._bg_color)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 1.5, 1.5)

        # 计算动画进度宽度
        progress_width = min(self.width() * (self._animated_progress / self._duration), self.width())

        # 绘制前景
        painter.setBrush(self._fg_color)
        painter.drawRoundedRect(0, 0, int(progress_width), self.height(), 1.5, 1.5)


class Plugin(PluginBase):
    """主插件实现类"""

    def __init__(self, cw_contexts, method):
        super().__init__(cw_contexts, method)
        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)
        update_signal.update_signal.connect(self.update_content)

        self.plugin_dir = self.cw_contexts['PLUGIN_PATH']
        self.font_loaded = False
        self._load_custom_font()

        # UI组件
        self.sse_client: Optional[SSEClient] = None
        self.cover_label: Optional[ImageLabel] = None
        self.title_label = None
        self.artist_label = None
        self.main_label = None
        self.sub_label = None
        self.progress_bar: Optional[ProgressBar] = None

        # 颜色跟踪
        self._current_bg = QColor()
        self._current_fg = QColor()
        self._update_progress_colors()

        # 保存上次歌曲信息
        self.last_song_name = ""
        self.last_artist = ""
        self.last_cover_url = ""

        # 封面加载重试计数器
        self.current_cover_retries = 0  # 当前封面加载尝试次数
        self.current_loading_url = ""   # 当前正在加载的封面URL

    def execute(self):
        """插件启动入口"""
        try:
            self._setup_ui()
            self._start_sse_client()
            logger.success('插件启动成功')
        except Exception as e:
            logger.error(f"启动失败: {str(e)}")

    def _load_custom_font(self):
        """加载自定义字体"""
        try:
            font_dir = os.path.join(self.plugin_dir, "font")
            font_path = os.path.join(font_dir, "HarmonyOS_Sans_SC_Regular.ttf")

            if not os.path.exists(font_path):
                logger.warning(f"字体文件不存在: {font_path}")
                return

            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id == -1:
                logger.error("字体加载失败")
                return

            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                logger.success(f"字体加载成功: {families[0]}")
                self.font_loaded = True
        except Exception as e:
            logger.error(f"字体加载异常: {str(e)}")

    def _update_progress_colors(self):
        """更新进度条颜色"""
        if isDarkTheme():
            self._current_bg = QColor(255, 255, 255, 50)
            self._current_fg = QColor(255, 255, 255, 200)
        else:
            self._current_bg = QColor(0, 0, 0, 30)
            self._current_fg = QColor(0, 0, 0, 150)

        if self.progress_bar:
            self.progress_bar.update_colors(self._current_bg, self._current_fg)

    def _setup_ui(self):
        """初始化用户界面"""
        widget = self.method.get_widget(WIDGET_CODE)
        if not widget:
            return

        if backgnd := widget.findChild(QFrame, 'backgnd'):
            backgnd.layout().setContentsMargins(0, 0, 0, 0)

        # 清理旧布局
        if title := widget.findChild(QLabel, 'title'):
            title.hide()
        if content_layout := widget.findChild(QHBoxLayout, 'contentLayout'):
            while content_layout.count():
                if item := content_layout.takeAt(0):
                    if item.widget():
                        item.widget().deleteLater()

            # 构建新布局
            main_layout = QHBoxLayout()
            main_layout.setContentsMargins(14, 6, 7, 6)
            main_layout.setSpacing(8)

            # 封面区域
            self.cover_label = ImageLabel()
            self.cover_label.setBorderRadius(6, 6, 6, 6)
            self.cover_label.setFixedSize(QSize(60, 60))
            main_layout.addWidget(self.cover_label)

            # 右侧信息区域
            right_layout = QVBoxLayout()
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(4)

            # 进度条
            self.progress_bar = ProgressBar()
            self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            right_layout.addWidget(self.progress_bar)

            # 歌曲信息
            self.title_label = QLabel("未知歌曲")
            self.artist_label = QLabel("未知歌手")
            self.song_info_layout = QHBoxLayout()
            self.song_info_layout.setAlignment(Qt.AlignLeft)
            self.song_info_layout.setSpacing(6)
            info_layout = QVBoxLayout()
            info_layout.setContentsMargins(0, 0, 0, 0)
            info_layout.setSpacing(0)
            info_layout.addLayout(self.song_info_layout)
            self.song_info_layout.addWidget(self.title_label)
            self.song_info_layout.addWidget(self.artist_label)
            right_layout.addLayout(info_layout)

            # 歌词区域
            lyrics_layout = QVBoxLayout()
            lyrics_layout.setContentsMargins(0, 0, 0, 0)
            lyrics_layout.setSpacing(0)
            self.main_label = QLabel(DEFAULT_LYRIC)
            self.sub_label = QLabel()
            lyrics_layout.addWidget(self.main_label, stretch=3)
            lyrics_layout.addWidget(self.sub_label, stretch=1)
            right_layout.addLayout(lyrics_layout)

            main_layout.addLayout(right_layout)
            content_layout.addLayout(main_layout)
            self._update_theme_styles()

    def _load_cover_image(self, url: str):
        """异步加载封面图片（带重试次数限制）"""
        try:
            # 检查当前URL是否仍然有效
            if url != self.current_loading_url:
                logger.debug("封面URL已变更，取消加载")
                return

            # 检查重试次数
            if self.current_cover_retries >= 5:
                logger.info("封面加载失败次数超过5次，不再尝试")
                self.cover_label.clear()
                return

            # 增加尝试次数
            self.current_cover_retries += 1
            logger.debug(f"开始加载封面，第{self.current_cover_retries}次尝试")

            if url.startswith("data:image/"):
                _, encoded = url.split(",", 1)
                image_data = base64.b64decode(encoded)
                pixmap = QPixmap.fromImage(QImage.fromData(image_data))
            elif url.startswith(("http://", "https://")):
                response = requests.get(url, timeout=3, proxies={'http': None, 'https': None})  # 禁用代理
                response.raise_for_status()
                pixmap = QPixmap.fromImage(QImage.fromData(response.content))
            else:
                return

            # 加载成功后重置计数器
            self.current_cover_retries = 0
            pixmap = pixmap.scaled(60, 60, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.cover_label.setImage(pixmap)
            logger.success("封面图片加载成功")

        except requests.exceptions.RequestException as e:
            logger.error(f"封面加载失败，第{self.current_cover_retries}次尝试 ({str(e)})")
            if self.current_cover_retries >= 5:
                self.cover_label.clear()
                logger.info("封面加载失败超过5次，停止尝试")
        except Exception as e:
            logger.error(f"封面处理异常: {str(e)}")
            self.cover_label.clear()

    def update_content(self, data: dict, widget_name: str):
        """更新UI内容"""
        if widget_name != WIDGET_NAME:
            return

        try:
            # 更新进度条
            duration = data.get('duration', 0.0)
            progress = data.get('progress', 0.0)
            if duration > 0 and progress >= 0:
                self.progress_bar.update_progress(progress, duration)
            else:
                self.progress_bar.update_progress(0, 1)

            # 获取当前歌曲信息
            current_song_name = data.get('title', '未知歌曲')
            current_artist = data.get('artist', '未知')
            current_cover_url = data.get('cover_url', '')

            # 检查歌曲信息是否变化
            if (current_song_name != self.last_song_name or
                    current_artist != self.last_artist or
                    current_cover_url != self.last_cover_url):

                # 重置重试计数器并记录当前加载URL
                self.current_cover_retries = 0
                self.current_loading_url = current_cover_url

                # 更新封面（仅当URL有效且尝试次数未超限时）
                if current_cover_url and self.current_cover_retries < 5:
                    threading.Thread(
                        target=self._load_cover_image,
                        args=(current_cover_url,),
                        daemon=True
                    ).start()
                else:
                    self.cover_label.clear()

                # 保存当前信息
                self.last_song_name = current_song_name
                self.last_artist = current_artist
                self.last_cover_url = current_cover_url

            # 文本信息更新（无论是否变化都更新显示）
            self.title_label.setText(current_song_name)
            self.artist_label.setText(f"歌手: {current_artist}")

            # 歌词处理
            lyrics = data.get('lyrics', '').strip()
            if not lyrics:
                main_text, sub_text = "●  ●  ●", ""
            else:
                parts = lyrics.split('\n', 1)
                main_text = parts[0].strip() if parts else "●  ●  ●"
                sub_text = parts[1].strip() if len(parts) > 1 else ""

            self.main_label.setText(main_text)
            self.sub_label.setText(sub_text)

            # 动态样式调整
            has_sub = bool(sub_text)
            is_dark = isDarkTheme()
            text_color = "#FFFFFF" if is_dark else "#333333"
            font_family = "'HarmonyOS Sans SC'" if self.font_loaded else "sans-serif"

            # 根据歌词行数调整字号
            main_style = f"""
                QLabel {{
                    font-family: {font_family};
                    color: {text_color};
                    font-weight: bold;
                    margin: 0;
                    font-size: {'20px' if has_sub else '24px'};
                }}
            """

            sub_style = f"""
                QLabel {{
                    font-family: {font_family};
                    color: {"#CCCCCC" if is_dark else "#666666"};
                    font-size: 14px;
                    margin: 0;
                }}
            """

            self.main_label.setStyleSheet(main_style)
            self.sub_label.setStyleSheet(sub_style)

            # 布局高度调整
            if has_sub:
                self.main_label.setFixedHeight(38)
                self.sub_label.setFixedHeight(14)
            else:
                self.main_label.setFixedHeight(30)
                self.sub_label.setFixedHeight(0)

            # 更新主题颜色
            self._update_progress_colors()

        except Exception as e:
            logger.error(f"更新失败: {str(e)}")

    def _update_theme_styles(self):
        """更新主题相关样式"""
        is_dark = isDarkTheme()
        text_color = "#FFFFFF" if is_dark else "#333333"
        sub_color = "#CCCCCC" if is_dark else "#666666"
        font_family = "'HarmonyOS Sans SC'" if self.font_loaded else "sans-serif"

        # 标题样式
        self.title_label.setStyleSheet(f"""
            QLabel {{
                color: {text_color};
                font: bold 13px {font_family};
                margin: 0;
                max-height: 20px;
            }}
        """)

        # 歌手样式
        self.artist_label.setStyleSheet(f"""
            QLabel {{
                color: {sub_color};
                font: 11px {font_family};
                margin: 0;
                max-height: 16px;
            }}
        """)

        # 歌词样式
        self.main_label.setStyleSheet(f"""
            QLabel {{
                font-family: {font_family};
                font-weight: bold;
                color: {text_color};
                margin: 0;
            }}
        """)

        # 扩展歌词样式
        self.sub_label.setStyleSheet(f"""
            QLabel {{
                font-family: {font_family};
                color: {sub_color};
                margin: 0;
            }}
        """)

    def _start_sse_client(self):
        """启动SSE客户端线程"""

        def worker():
            try:
                self.sse_client = SSEClient(SSE_URL)
                self.sse_client.start()
            except Exception as e:
                logger.error(f"SSE连接异常: {str(e)}")

        threading.Thread(target=worker, daemon=True).start()

    def cleanup(self):
        """清理资源 未引用"""
        if self.sse_client:
            self.sse_client.stop()
