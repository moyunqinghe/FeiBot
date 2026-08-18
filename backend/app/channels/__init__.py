"""渠道层:各消息平台的协议接入与收发循环。

本层只做协议交互和循环管理;业务决策(指令、回复内容)全在 agent/ 层,
通过回调注入(见 ingress.run_wechat_ingress 的 on_message 参数)。
"""

from __future__ import annotations
