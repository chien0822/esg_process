"""流程控制用的例外。"""

from __future__ import annotations


class StageNotImplemented(Exception):
    """該階段尚未實作（依里程碑逐步完成）。"""


class ReportStop(Exception):
    """該份報告書正常停止（不是程式錯誤），例如找不到策略表。"""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class RunSetupError(Exception):
    """執行前的設定或參數錯誤，訊息會直接給使用者看。"""
