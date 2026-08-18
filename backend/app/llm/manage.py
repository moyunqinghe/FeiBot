"""模型配置管理 CLI。

用法:
    python -m app.llm.manage add --name claude --protocol anthropic_messages \
        --model claude-sonnet-4-5 --base-url https://api.anthropic.com --api-key sk-xxx
    python -m app.llm.manage list          # 列出全部(key 脱敏,* 为当前)
    python -m app.llm.manage use claude    # 切换当前模型
    python -m app.llm.manage remove claude # 删除
"""

from __future__ import annotations

import argparse

from app.llm import registry


def _cmd_add(args: argparse.Namespace) -> None:
    registry.add_model(
        name=args.name,
        protocol=args.protocol,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
    )
    print(f"已保存模型配置:{args.name}")


def _cmd_list(_args: argparse.Namespace) -> None:
    configs = registry.list_models()
    if not configs:
        print("(无模型配置;用 add 子命令添加,或在微信里发 /模型 查看)")
        return
    current = registry.get_current_name()
    for record in configs:
        mark = "*" if record.name == current else " "
        print(
            f"{mark} {record.name}  protocol={record.api_protocol}"
            f"  model={record.model}  base_url={record.base_url}"
            f"  key={registry.masked_key(record)}  temperature={record.temperature}"
        )


def _cmd_use(args: argparse.Namespace) -> None:
    if registry.set_current(args.name):
        print(f"已切换到模型:{args.name}")
    else:
        print(f"没有名为「{args.name}」的模型配置;用 list 查看已有配置")


def _cmd_remove(args: argparse.Namespace) -> None:
    if registry.remove_model(args.name):
        print(f"已删除模型配置:{args.name}")
    else:
        print(f"没有名为「{args.name}」的模型配置")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.llm.manage", description="管理 LLM 模型配置"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="添加/覆盖模型配置")
    p_add.add_argument("--name", required=True, help="备注名(主键)")
    p_add.add_argument("--protocol", required=True, choices=registry.PROTOCOL_NAMES)
    p_add.add_argument("--model", required=True, help="provider 侧的模型名")
    p_add.add_argument("--base-url", required=True)
    p_add.add_argument("--api-key", required=True)
    p_add.add_argument("--temperature", type=float, default=1.0)
    p_add.set_defaults(func=_cmd_add)

    p_list = sub.add_parser("list", help="列出全部配置(key 脱敏)")
    p_list.set_defaults(func=_cmd_list)

    p_use = sub.add_parser("use", help="切换当前模型")
    p_use.add_argument("name")
    p_use.set_defaults(func=_cmd_use)

    p_remove = sub.add_parser("remove", help="删除模型配置")
    p_remove.add_argument("name")
    p_remove.set_defaults(func=_cmd_remove)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        parser.exit(2, f"错误:{exc}\n")


if __name__ == "__main__":
    main()
