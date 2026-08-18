# llm-protocols

LLM 提供商的**通用纯协议层**封装,零业务/数据库/Web 框架耦合。
覆盖四种协议:OpenAI `chat.completions`、OpenAI `responses`、
Anthropic `messages`、Gemini `generateContent`。

- 不依赖任何业务代码,不含数据库、不含 fastapi/sqlmodel——所有参数显式传入。
- 依赖:`httpx`、`openai`、`anthropic`、`cryptography`、`pydantic`。
- 要求 Python >= 3.11。

## 安装

```bash
# 在新项目中直接以本地路径安装
pip install /path/to/app/llm/llm_protocols

# 或以可编辑模式安装(调试用)
pip install -e /path/to/app/llm/llm_protocols

# 跑测试
pip install "/path/to/app/llm/llm_protocols[test]"
pytest /path/to/app/llm/llm_protocols/tests
```

## 快速上手

```python
from llm_protocols import (
    LLMClient,
    derive_fernet_key,
    encrypt_secret,
    snapshot_model_config,
)

SECRET = "my-app-secret"  # 宿主自己的应用密钥,决定密文的钥匙
key = derive_fernet_key(SECRET)

# 1. 配置落库前加密 API key(密文可跨项目互通:算法为 b64(sha256(secret)))
record = {
    "api_key_encrypted": encrypt_secret("sk-...", key),
    "api_protocol": "anthropic_messages",
    "base_url": "https://api.anthropic.com",
    "model": "claude-sonnet-4-5",
    "temperature": 0.2,
    "max_output_tokens": 8192,
}

# 2. 运行时:鸭子类型快照(冻结 protocol_options / extra_body,保证只读)
config = snapshot_model_config(type("Row", (), record)())

# 3. 装配客户端(secret 显式传入,用于解密 api_key_encrypted)
client = LLMClient(config, secret=SECRET)

# 4. 组装请求并直调 driver;不做重试/span/stage(这些是宿主职责)
request = client.build_request(
    [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "ping"},
    ]
)
completion = client.complete(request)
print(completion.choices[0].message.content)
```

## 四种协议

`ModelApiProtocol` 四个取值对应四个 driver,输入都是**规范化请求 dict**
(`model` / `messages` / `temperature` / `max_tokens`,可选
`response_format={"type": "json_object"}`、`_cancellation`),输出统一归一化为
chat-completion 形状的 `SimpleNamespace`(`.choices[0].message.content`、
`.usage.prompt_tokens` 等):

| 协议 | driver | 传输 |
| --- | --- | --- |
| `openai_chat_completions` | `ChatCompletionsDriver` | openai SDK |
| `openai_responses` | `OpenAIResponsesDriver` | openai SDK(`responses.create`) |
| `anthropic_messages` | `AnthropicMessagesDriver` | anthropic SDK(自动剥离 base_url 的 `/v1` 或 `/v1/messages` 后缀) |
| `gemini_generate_content` | `GeminiGenerateContentDriver` | 原生 httpx(自动补 `/v1beta`) |

共同的边界行为(原样保留自 StaffDeck 实现):

- 图片仅接受 `data:image/(jpeg|png|gif|webp);base64,...`,单张 ≤ 5 MiB,
  总数 ≤ 6,合计 ≤ 18 MiB,整个请求 ≤ 25 MiB;超限抛
  `ValueError("MODEL_IMAGE_TOO_LARGE" / "MODEL_TOO_MANY_IMAGES" / ...)`。
- Claude 5 系模型(`claude-sonnet-5*` / `claude-opus-5*`)自动省略
  `temperature`(LLM Center 的 Claude 5 部署拒绝旧采样字段)。
- 错误统一分类为 `ProtocolCallError`(`code` 如 `MODEL_RATE_LIMITED`、
  `retryable`、`provider_code/message`、`upstream_body`、`request_id`);
  上游响应体中的 api_key/authorization/token/secret 一律脱敏为 `[redacted]`。
- 取消:请求 dict 里放 `_cancellation=CancellationToken()`,驱动在每次
  收发前检查,取消时抛 `ProtocolCallError("MODEL_CANCELLED")`。

## ModelConfig 字段

`snapshot_model_config` / `LLMClient` 接受鸭子类型对象(ORM 行、
`SimpleNamespace`、`ResolvedModelConfig` 均可),字段如下:

- 必需:`api_protocol`(四值之一,缺省 `openai_chat_completions`)、
  `api_key_encrypted`(用 `encrypt_secret` 加密的密文)、`base_url`、
  `model`、`temperature`、`max_output_tokens`
- 可选:`id`、`tenant_id`、`name`、`purpose`(`"runtime"`/`"verification"`)、
  `timeout_seconds`、`protocol_options`(按协议分组的 dict,
  如 `{"openai_chat_completions": {"thinking": {"type": "enabled"}}}`)、
  `legacy_extra_body` / `extra_body_json`、`config_revision`、`security_revision`

校验类错误抛 `ModelProtocolError`(原实现的 HTTP 422),`.code` 保留原
detail 字符串:`MODEL_PROVIDER_UNSUPPORTED`、`MODEL_PROTOCOL_UNSUPPORTED`、
`MODEL_PROTOCOL_CONFLICT`、`MODEL_PROTOCOL_OPTIONS_INVALID`、
`MODEL_BASE_URL_INVALID`。

## 本包的边界:使用方需要自己决定的事

本包只负责"怎么跟模型提供商说话"(协议层)。以下问题没有标准答案,
每个接入项目必须根据自己的业务自行实现:

1. **配置存哪**
   模型配置落不落库、表结构、多租户隔离、`config_revision` /
   `security_revision` 怎么递增,都是宿主的存储设计。本包只提供
   `ResolvedModelConfig` 值对象、`snapshot_model_config` 快照和
   `model_config_fingerprint` 指纹计算。

2. **验证与信任门控**
   原实现中"配置必须验证通过(指纹匹配)才能跑 runtime"的信任状态机
   (`resolve_model_config_for_runtime / _for_verification`)绑定 ORM,
   **有意不抽**。宿主自行决定:未验证的配置是否允许调用、验证流程怎么编排。

3. **可观测性**
   span、stage、指标上报全部留给宿主。本包的 `complete/stream` 直调 driver,
   不打点;`usage_metrics` 只负责把各家 usage 归一化成 dict,怎么用随你。

4. **重试编排**
   空响应重试、reasoning 模型的 token 预算升档、指数退避等都不在本包内。
   `ProtocolCallError.retryable` 和 `LLMError.retryable` 已经标好"值不值得
   重试",重试策略由宿主决定。

5. **消息组装与裁剪时机**
   `fit_request_messages`(token 预算裁剪)、`thinking_request_kwargs`、
   `extract_json` / `loads_llm_json`(JSON 修复)都是纯函数工具,
   何时调用、组装什么样的 messages,是宿主的编排逻辑。

### 从 StaffDeck 剥离的耦合点

- `protocols.py`:`fastapi.HTTPException(422)` → `ModelProtocolError`(code 不变)。
- `crypto.py`:`app.config.get_settings().app_secret` → 显式传 secret/key。
- `config.py`:删除 sqlmodel `Session` 与信任状态机,只留值对象与快照。
- `factory.py` / `client.py`:超时与 thinking 模式不再读全局 settings,
  改为构造参数;解密用显式 secret。
- `utils.py` 与 `drivers.py`:本来就零耦合,原样保留(仅私有函数改名导出)。

## 凭证安全存储

`api_key` 是长期凭证,落库前建议加密。密钥派生算法为
`b64(sha256(secret))`:同一个 secret 串在任何项目中派生出同一把 key,
加密结果可跨项目互通(与 StaffDeck 的存量密文位兼容)。

```python
from llm_protocols import derive_fernet_key, encrypt_secret, decrypt_secret

key = derive_fernet_key("my-app-secret")
enc = encrypt_secret("sk-...", key)   # 加密后存数据库
assert decrypt_secret(enc, key) == "sk-..."  # 使用时解密
```

## API 摘要

### `llm_protocols.drivers`

- 四个 driver(见上表),均为 frozen dataclass,注入客户端对象后调用
  `complete(request)` / `stream(request)`。
- `ProtocolCallError` — 统一错误:`code`、`retryable`、`status_code`、
  `provider_code`、`provider_message`、`upstream_body`、`request_id`。
- `CancellationToken` — 线程安全的取消令牌。
- `ProtocolDriver` — 结构化 Protocol(`request_kind` + complete/stream)。

### `llm_protocols.protocols`

- `ModelApiProtocol`、`available_model_protocols()`、`resolve_api_protocol`、
  `normalize_chat_protocol_options`、`current_protocol_options`、
  `model_config_fingerprint`、`validate_model_base_url`、
  `LEGACY_OPENAI_PROVIDER`、`ModelProtocolError`。

### `llm_protocols.config`

- `ResolvedModelConfig` — frozen dataclass 值对象。
- `snapshot_model_config(model_config, *, min_output_tokens=0)` — 鸭子类型快照,
  `protocol_options` / `legacy_extra_body` 深拷贝并冻结为只读 Mapping。

### `llm_protocols.crypto`

- `derive_fernet_key(secret)`、`encrypt_secret(value, key)`、
  `decrypt_secret(value, key)`、`mask_secret(value)`。

### `llm_protocols.factory`

- `build_protocol_driver(*, protocol, api_key, base_url, model, timeout_seconds)`
  → `(sdk_client, driver)`;含 Anthropic `/v1` 后缀剥离 hack。
  `api_key` 必须是解密后的明文。

### `llm_protocols.client`

- `LLMError` — 宿主侧统一异常,`public_detail()` 给出可外发的错误 dict。
- `LLMClient(model_config, *, secret, default_timeout_seconds=600.0,
  thinking_mode="", thinking_models="")` — 装配 + `build_request` +
  `complete` / `stream` 透传,无任何编排。

### `llm_protocols.utils`

- JSON:`extract_json`、`loads_llm_json`(围栏剥离、尾逗号修复、
  字符串内容修复、`ast.literal_eval` 兜底)。
- 裁剪:`fit_request_messages(messages, token_budget=32000)`、
  `request_tokens`、`content_text`、`TURN_STAGE_MESSAGE_MARKER`。
- thinking:`thinking_request_kwargs(mode, extra_body)`、
  `thinking_mode_from_extra_body`、`thinking_mode_for_model`、
  `normalize_thinking_mode`、`normalize_extra_body`。
- usage:`usage_metrics(usage)` → `{input_tokens, output_tokens, total_tokens,
  cached_input_tokens, uncached_input_tokens}`(各家字段名归一)。

### `llm_protocols.schemas`

- 纯 pydantic 模型:`ModelConfigCreateRequest` / `ModelConfigUpdateRequest` /
  `ModelConfigRead` / `ModelConfigTestResponse` / `ModelCapabilityTestResult` /
  `ModelProviderErrorDetail`(原样保留,可直接用于宿主自己的 API 层)。
