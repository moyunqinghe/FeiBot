from __future__ import annotations

from types import SimpleNamespace

from llm_protocols.drivers import OpenAIResponsesDriver
from llm_protocols.factory import build_protocol_driver


class _ClosableEvents(list):
    def __init__(self, values) -> None:  # noqa: ANN001
        super().__init__(values)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Responses:
    def __init__(self, response=None, events=None) -> None:  # noqa: ANN001
        self.calls = []
        self.response = response
        self.events = events

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.events if kwargs.get("stream") else self.response


def _request(**overrides):  # noqa: ANN003
    value = {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        "temperature": 0.2,
        "max_tokens": 256,
    }
    value.update(overrides)
    return value


def test_responses_driver_maps_request_and_completion() -> None:
    response = SimpleNamespace(
        id="resp_123",
        status="completed",
        output=[
            SimpleNamespace(type="reasoning", content=[]),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="hello"),
                    SimpleNamespace(type="output_text", text=" world"),
                ],
            ),
        ],
        usage=SimpleNamespace(input_tokens=12, output_tokens=3, total_tokens=15),
    )
    responses = _Responses(response=response)
    driver = OpenAIResponsesDriver(SimpleNamespace(responses=responses))

    result = driver.complete(_request(response_format={"type": "json_object"}))

    assert result.id == "resp_123"
    assert result.choices[0].message.content == "hello world"
    assert result.choices[0].finish_reason == "stop"
    assert result.usage.prompt_tokens == 12
    assert responses.calls == [
        {
            "model": "gpt-test",
            "input": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
            "temperature": 0.2,
            "max_output_tokens": 256,
            "store": False,
            "text": {"format": {"type": "json_object"}},
        }
    ]


def test_responses_driver_maps_multimodal_content() -> None:
    response = {
        "id": "resp_image",
        "status": "completed",
        "output": [{"type": "message", "content": []}],
        "usage": {},
    }
    responses = _Responses(response=response)
    driver = OpenAIResponsesDriver(SimpleNamespace(responses=responses))

    driver.complete(
        _request(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                    ],
                }
            ]
        )
    )

    assert responses.calls[0]["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "describe"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                },
            ],
        }
    ]


def test_responses_driver_maps_typed_stream_events() -> None:
    usage = SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6)
    events = _ClosableEvents(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp_stream"),
            ),
            SimpleNamespace(type="response.output_text.delta", delta="stream"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="resp_stream",
                    status="completed",
                    usage=usage,
                ),
            ),
        ]
    )
    responses = _Responses(events=events)
    driver = OpenAIResponsesDriver(SimpleNamespace(responses=responses))

    chunks = list(driver.stream(_request()))

    assert chunks[1].choices[0].delta.content == "stream"
    assert chunks[2].choices[0].finish_reason == "stop"
    assert chunks[2].usage.completion_tokens == 2
    assert responses.calls[0]["stream"] is True
    assert events.closed is True


def test_factory_builds_responses_driver(monkeypatch) -> None:
    responses = _Responses()
    fake_client = SimpleNamespace(responses=responses)
    captured = {}
    monkeypatch.setattr(
        "llm_protocols.factory.OpenAI",
        lambda **kwargs: captured.update(kwargs) or fake_client,
    )

    sdk_client, driver = build_protocol_driver(
        protocol="openai_responses",
        api_key="secret",
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        timeout_seconds=30.0,
    )

    assert isinstance(driver, OpenAIResponsesDriver)
    assert sdk_client is fake_client
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert captured["timeout"] == 30.0
