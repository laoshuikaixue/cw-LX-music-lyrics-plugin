import base64
import json
import os
import threading
from typing import Optional

import requests
from PyQt5.QtCore import QObject, pyqtSignal, Qt, QSize
from PyQt5.QtGui import QPixmap, QImage, QFontDatabase
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
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
    def _parse_event_data(event_data: str) -> tuple[str, str]:
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
                params={'filter': 'lyricLineAllText,name,singer,picUrl'},
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
        if event_type == 'lyricLineAllText':
            music_data.lyrics_text = data
        elif event_type == 'name':
            music_data.song_name = data
        elif event_type == 'singer':
            music_data.artist = data
        elif event_type == 'picUrl':
            music_data.cover_url = data

        update_signal.update_signal.emit({
            'lyrics': music_data.lyrics_text,
            'title': music_data.song_name,
            'artist': music_data.artist,
            'cover_url': music_data.cover_url
        }, WIDGET_NAME)

    def stop(self):
        """停止SSE连接"""
        self.running = False
        self.session.close()


class Plugin(PluginBase):
    """主插件实现类"""

    def __init__(self, cw_contexts, method):
        super().__init__(cw_contexts, method)
        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)
        update_signal.update_signal.connect(self.update_content)

        # 若要引用插件目录的内容，需在目录前添加插件的工作目录：
        self.plugin_dir = self.cw_contexts['PLUGIN_PATH']

        # 字体加载
        self.font_loaded = False
        self._load_custom_font()

        # UI组件
        self.sse_client: Optional[SSEClient] = None
        self.cover_label: Optional[ImageLabel] = None
        self.title_label = None
        self.artist_label = None
        self.main_label = None
        self.sub_label = None

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
            # 构建字体路径
            font_dir = os.path.join(self.plugin_dir, "font")
            font_path = os.path.join(font_dir, "HarmonyOS_Sans_SC_Regular.ttf")

            # 验证字体文件存在性
            if not os.path.exists(font_path):
                logger.warning(f"字体文件不存在: {font_path}")
                return

            # 加载字体
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id == -1:
                logger.error("字体加载失败，请检查文件格式")
                return

            # 获取字体族名称
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                logger.error("字体文件中未找到有效字体族")
                return

            logger.success(f"字体加载成功: {families[0]}")
            self.font_loaded = True

        except Exception as e:
            logger.error(f"字体加载异常: {str(e)}")

    def _setup_ui(self):
        """初始化用户界面"""
        widget = self.method.get_widget(WIDGET_CODE)
        if not widget:
            return

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
            main_layout.setContentsMargins(4, 2, 4, 1)
            main_layout.setSpacing(8)

            # 封面区域（初始为空）
            self.cover_label = ImageLabel()
            self.cover_label.setBorderRadius(6, 6, 6, 6)
            self.cover_label.setFixedSize(QSize(60, 60))
            main_layout.addWidget(self.cover_label)

            # 右侧信息区域
            right_layout = QVBoxLayout()
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(1)

            # 歌曲信息
            self.title_label = QLabel("未知歌曲")
            self.artist_label = QLabel("未知歌手")
            info_layout = QVBoxLayout()
            info_layout.setContentsMargins(0, 0, 0, 0)
            info_layout.setSpacing(0)
            info_layout.addWidget(self.title_label)
            info_layout.addWidget(self.artist_label)
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
        """异步加载封面图片"""
        try:
            if url.startswith("data:image/"):
                _, encoded = url.split(",", 1)
                image_data = base64.b64decode(encoded)
                pixmap = QPixmap.fromImage(QImage.fromData(image_data))
            elif url.startswith(("http://", "https://")):
                response = requests.get(url, timeout=3)
                response.raise_for_status()
                pixmap = QPixmap.fromImage(QImage.fromData(response.content))
            else:
                return

            pixmap = pixmap.scaled(60, 60, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.cover_label.setImage(pixmap)
        except Exception as e:
            self.cover_label.clear()  # 加载失败时清空显示
            logger.error(f"封面加载失败: {str(e)}")

    def update_content(self, data: dict, widget_name: str):
        """更新UI内容"""
        if widget_name != WIDGET_NAME:
            return

        try:
            # 封面更新
            if new_cover_url := data.get('cover_url'):
                threading.Thread(
                    target=self._load_cover_image,
                    args=(new_cover_url,),
                    daemon=True
                ).start()
            else:
                self.cover_label.clear()

            # 文本信息更新
            self.title_label.setText(data.get('title', '未知歌曲'))
            self.artist_label.setText(f"歌手: {data.get('artist', '未知')}")

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

        except Exception as e:
            logger.error(f"更新失败: {str(e)}")

    def _update_theme_styles(self):
        """更新主题相关样式"""
        is_dark = isDarkTheme()
        text_color = "#FFFFFF" if is_dark else "#333333"
        sub_color = "#CCCCCC" if is_dark else "#666666"

        # 使用字体族名
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
