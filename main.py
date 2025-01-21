import json
import threading
from typing import Optional

import requests
from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget, QVBoxLayout, QSizePolicy, QLayout
from loguru import logger
from qfluentwidgets import isDarkTheme

from .ClassWidgets.base import PluginBase

WIDGET_CODE = 'lx-music-lyrics.ui'
WIDGET_NAME = 'LX-music-Lyrics'
WIDGET_WIDTH = 300

SSE_URL = 'http://127.0.0.1:23330/subscribe-player-status'
DEFAULT_LYRIC = '等待音乐软件侧传输歌词...'


class UpdateSignal(QObject):
    update_signal = pyqtSignal(str, str)


class LyricsData:
    def __init__(self):
        self.lyrics_text = ""


lyrics_data = LyricsData()
update_signal = UpdateSignal()


class SSEClient:
    def __init__(self, url: str):
        self.url = url
        self.running = False
        self.session = requests.Session()

    @staticmethod
    def _process_event(event_data: str) -> tuple[str, str]:
        event_type = ""
        data = ""

        for line in event_data.split('\n'):
            line = line.strip()
            if line.startswith('event:'):
                event_type = line[6:].strip()
            elif line.startswith('data:'):
                data = line[5:].strip()
                try:
                    if data.startswith('"') and data.endswith('"'):
                        data = json.loads(data)
                except json.JSONDecodeError:
                    pass

        return event_type, data

    def start(self):
        self.running = True
        try:
            headers = {
                'Accept': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive'
            }

            response = self.session.get(
                self.url,
                headers=headers,
                params={'filter': 'lyricLineAllText'},
                stream=True
            )
            response.encoding = 'utf-8'
            response.raise_for_status()

            event_data = []

            for line in response.iter_lines(decode_unicode=True):
                if not self.running:
                    break

                if line:
                    event_data.append(line)
                elif event_data:
                    event_type, data = self._process_event('\n'.join(event_data))
                    if event_type == 'lyricLineAllText':
                        lyrics_data.lyrics_text = data
                        update_signal.update_signal.emit(
                            lyrics_data.lyrics_text,
                            WIDGET_NAME
                        )
                    event_data = []

        except requests.RequestException as e:
            logger.error(f"SSE 连接错误: {str(e)}")
            if self.running:
                logger.info("5秒后尝试重新连接...")
                threading.Timer(5.0, self.start).start()
        except Exception as e:
            logger.error(f"未预期的错误: {str(e)}")
        finally:
            if not self.running:
                self.stop()

    def stop(self):
        self.running = False
        self.session.close()


class Plugin(PluginBase):  # 插件类
    def __init__(self, cw_contexts, method):  # 初始化
        super().__init__(cw_contexts, method)  # 调用父类初始化方法

        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)  # 注册小组件到CW

        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)
        self.sse_client: Optional[SSEClient] = None
        update_signal.update_signal.connect(self.update_content)
        self.lyrics_widget = None
        self.main_label = None
        self.sub_label = None

    def execute(self):
        try:
            self._setup_widget()
            self._start_sse_client()
            if self.lyrics_widget:
                title = self.lyrics_widget.findChild(QLabel, 'title')
                title.hide()
            logger.success('歌词插件启动成功！')
        except Exception as e:
            logger.error(f"插件启动失败: {str(e)}")
            raise

    def _setup_widget(self):
        self.lyrics_widget = self.method.get_widget(WIDGET_CODE)
        if self.lyrics_widget:
            content_layout = self.lyrics_widget.findChild(QHBoxLayout, 'contentLayout')
            if content_layout:
                # 清除content_layout中的所有项
                while content_layout.count():
                    item = content_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()

                # 创建容器
                self.container_widget = QWidget()
                container_layout = QVBoxLayout(self.container_widget)
                container_layout.setSpacing(2)
                container_layout.setContentsMargins(10, 0, 10, 0)  # 添加左右边距

                # 创建主要歌词标签
                self.main_label = QLabel(DEFAULT_LYRIC)
                self.main_label.setWordWrap(False)  # 禁用自动换行
                self.main_label.setAlignment(Qt.AlignCenter)
                self.main_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

                # 创建扩展歌词标签
                self.sub_label = QLabel("")
                self.sub_label.setWordWrap(False)  # 禁用自动换行
                self.sub_label.setAlignment(Qt.AlignCenter)
                self.sub_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.sub_label.hide()

                # 设置容器的大小策略
                self.container_widget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

                # 添加标签到布局
                container_layout.addWidget(self.main_label)
                container_layout.addWidget(self.sub_label)

                # 设置高度
                self.container_widget.setFixedHeight(50)

                # 添加容器到主布局
                content_layout.addWidget(self.container_widget)

                # 设置content_layout的属性
                content_layout.setSizeConstraint(QLayout.SetMinimumSize)

                self._update_label_styles()
            else:
                logger.warning("未找到内容布局")
        else:
            logger.warning("未找到小组件")

    def update_content(self, lyrics_text: str):
        if not self.main_label or not self.sub_label:
            return

        try:
            if not lyrics_text.strip():
                main_text = "●  ●  ●"
                sub_text = ""
            else:
                lyrics_parts = lyrics_text.split('\n', 1)
                main_text = lyrics_parts[0] if lyrics_parts else ""
                sub_text = lyrics_parts[1] if len(lyrics_parts) > 1 else ""

            self.main_label.setText(main_text)

            if sub_text:
                self.sub_label.setText(sub_text)
                self.sub_label.show()
                self.main_label.setFixedHeight(25)
                self.sub_label.setFixedHeight(25)

                # 计算所需的最小宽度
                main_width = self.main_label.fontMetrics().boundingRect(main_text).width() + 40
                sub_width = self.sub_label.fontMetrics().boundingRect(sub_text).width() + 40
                min_width = max(main_width, sub_width)

                # 设置容器的最小宽度
                self.container_widget.setMinimumWidth(min_width)
            else:
                self.sub_label.hide()
                self.main_label.setFixedHeight(50)

                # 计算单行歌词所需的最小宽度
                min_width = self.main_label.fontMetrics().boundingRect(main_text).width() + 40
                self.container_widget.setMinimumWidth(min_width)

        except Exception as e:
            logger.error(f"更新内容失败: {str(e)}")

    def _update_label_styles(self):
        if not self.main_label or not self.sub_label:
            return

        is_dark = isDarkTheme()
        main_color = "#FFFFFF" if is_dark else "#000000"
        sub_color = "#CCCCCC" if is_dark else "#666666"

        # 主歌词样式
        self.main_label.setStyleSheet(f"""
            QLabel {{
                color: {main_color};
                font-family: "HarmonyOS Sans SC Bold", "Microsoft YaHei", "微软雅黑";
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
            }}
        """)

        # 扩展歌词样式
        self.sub_label.setStyleSheet(f"""
            QLabel {{
                color: {sub_color};
                font-family: "HarmonyOS Sans SC Bold", "Microsoft YaHei", "微软雅黑";
                font-size: 16px;
                font-weight: bold;
                padding: 0px;
                margin: 0px;
            }}
        """)

    def _start_sse_client(self):
        def sse_worker():
            try:
                self.sse_client = SSEClient(SSE_URL)
                self.sse_client.start()
            except Exception as e:
                logger.error(f"SSE 客户端错误: {str(e)}")

        sse_thread = threading.Thread(target=sse_worker, daemon=True)
        sse_thread.start()

    def theme_changed(self):
        self._update_label_styles()

    def cleanup(self):
        if self.sse_client:
            try:
                self.sse_client.stop()
                logger.info("SSE 客户端已停止")
            except Exception as e:
                logger.error(f"停止 SSE 客户端失败: {str(e)}")
