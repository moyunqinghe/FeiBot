"""agent 编排层:指令解析、会话、上下文、LLM 调用。

本层只做业务决策,不 import 渠道层与协议包(零渠道依赖);
渠道协议交互全部在 channels/ingress.py。
"""
