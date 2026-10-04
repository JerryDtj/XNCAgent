"""联网搜索、冷启动与三层情绪。理解结果按 §3.8 桩进 _prepare_turn，不打真实 LLM / 博查。"""

import asyncio
import json
import urllib.request
from contextlib import contextmanager

import pytest

from xncagent.agent import xiaoxizi_agent as agent
from xncagent.config.prompts import load_system_prompt
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.tools.plugin import PluginResult
from xncagent.tools.web_search_plugin import BochaProvider, TavilyProvider

_INJECTED_WEB = "下面是奴才刚上网为主人查到的资料"
_COLD = "这是本会话的第一轮。按规则 5 输出开场白（整场只允许这一次）。"
_ADVICE = "# 本轮劝谏"
_SILENCE = "# 本轮安全树洞"
_SILENCE_ACT = "说奴才把嘴闭上了、递上虚拟纸巾、安静陪着直到主人重新开口。不要劝，不要问。"


def _rewrite(**overrides) -> RewriterQuestionResponse:
    data = {
        "rewritten_query": "改写后的完整问题",
        "confidence": 0.9,
        "scene": "无关闲聊",
        "emotion_alert": False,
        "recall_query": "",
        "needs_web_search": False,
        "needs_silence": False,
    }
    data.update(overrides)
    return RewriterQuestionResponse.model_validate(data)


class SearchSpy:
    def __init__(self, result: PluginResult | None = None, boom: BaseException | None = None):
        self.calls: list[dict] = []
        self.result = result or PluginResult(ok=True, data={"hits": []})
        self.boom = boom
        self.provider_name = "bocha"
        self.count = 8

    def run(self, args: dict) -> PluginResult:
        self.calls.append(args)
        if self.boom is not None:
            raise self.boom
        return self.result


def _install(monkeypatch, understand, spy: SearchSpy, message_count=None, count_raises: BaseException | None = None):
    monkeypatch.setattr(agent, "_understand", understand)
    monkeypatch.setattr(agent, "retrieve_knowledge", lambda *args, **kwargs: "话术样本")
    monkeypatch.setattr(agent, "_maybe_play_music", lambda **kwargs: None)
    monkeypatch.setattr(agent, "_recall_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(agent, "get_history_by_session", lambda *args, **kwargs: "")
    # 降级分支的本地匹配。这里桩掉 bge，避免验收用例去拉模型。
    monkeypatch.setattr(agent, "match_scene", lambda query: ("捧哏金句", 0.8))
    monkeypatch.setattr(agent, "get_plugin", lambda name: spy if name == "web_search" else None)
    if count_raises is not None:
        def _boom(session_id):
            raise count_raises
        monkeypatch.setattr(agent, "get_session_message_count", _boom)
    elif message_count is not None:
        monkeypatch.setattr(agent, "get_session_message_count", lambda session_id: message_count)


def _turn(query: str, session_id=None, user_id=None):
    return asyncio.run(agent._prepare_turn(query, session_id, user_id))


@contextmanager
def _info_logs():
    lines: list[str] = []
    sink = agent.logger.add(
        lambda message: lines.append(message.record["message"]),
        level="INFO",
    )
    try:
        yield lines
    finally:
        agent.logger.remove(sink)


def test_old_rewrite_json_without_needs_web_search_defaults_false():
    parsed = RewriterQuestionResponse.model_validate(
        {
            "rewritten_query": "你好",
            "confidence": 0.4,
            "scene": "无关闲聊",
            "emotion_alert": False,
        }
    )
    assert parsed.needs_web_search is False
    assert parsed.needs_silence is False
    assert parsed.recall_query == ""


def test_null_needs_web_search_does_not_fail_validation():
    parsed = RewriterQuestionResponse.model_validate(
        {
            "rewritten_query": "你好",
            "confidence": 0.4,
            "scene": "无关闲聊",
            "emotion_alert": False,
            "needs_web_search": None,
            "needs_silence": None,
        }
    )
    assert parsed.needs_web_search is False
    assert parsed.needs_silence is False


def test_understand_prompt_states_web_search_rules():
    text = load_system_prompt("understand_query")
    assert "联网判定" in text
    assert "指涉对象" in text
    assert "时效性信息" in text
    assert "五场景话术类输入" in text
    assert "recall_query，不搜索" in text
    assert '"needs_web_search": false' in text


def test_case1_proper_noun_searches_original_query_and_injects_hits(monkeypatch):
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={
                "hits": [
                    {
                        "title": "奶绿波",
                        "snippet": "一种奶茶梗",
                        "url": "https://example.com/nailv",
                    }
                ]
            },
        )
    )

    def understand(query, history):
        return _rewrite(scene="无关闲聊", needs_web_search=True, rewritten_query="奶绿波是什么")

    _install(monkeypatch, understand, spy)
    prepared = _turn("你知道奶绿波吗")
    assert prepared["degraded"] is False
    assert spy.calls == [{"query": "你知道奶绿波吗", "count": 8}]
    system = prepared["messages"][0]["content"]
    assert _INJECTED_WEB in system
    assert "《奶绿波》" in system
    assert "一种奶茶梗" in system
    assert "来源：https://example.com/nailv" in system
    assert "奶绿波是什么" not in system


def test_case2_laughter_does_not_search(monkeypatch):
    spy = SearchSpy()

    def understand(query, history):
        return _rewrite(scene="捧哏金句", needs_web_search=False)

    _install(monkeypatch, understand, spy)
    prepared = _turn("哈哈哈")
    assert spy.calls == []
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]
    assert prepared["scene"] == "捧哏金句"


def test_case3_timely_news_searches(monkeypatch):
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={"hits": [{"title": "今日要闻", "snippet": "摘要", "url": "https://news.example"}]},
        )
    )

    def understand(query, history):
        return _rewrite(scene="无关闲聊", needs_web_search=True)

    _install(monkeypatch, understand, spy)
    prepared = _turn("今天有什么新闻")
    assert spy.calls[0]["query"] == "今天有什么新闻"
    assert "《今日要闻》" in prepared["messages"][0]["content"]


def test_case4_workplace_complaint_does_not_search(monkeypatch):
    spy = SearchSpy()

    def understand(query, history):
        return _rewrite(scene="职场吐槽", needs_web_search=False)

    _install(monkeypatch, understand, spy)
    prepared = _turn("老板又让我改方案")
    assert spy.calls == []
    assert prepared["scene"] == "职场吐槽"
    assert prepared["degraded"] is False
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]
    assert "话术样本" in prepared["messages"][0]["content"]


def test_case5_degraded_turn_skips_search_and_still_builds_reply(monkeypatch):
    spy = SearchSpy()

    def understand(query, history):
        raise RuntimeError("llm down")

    _install(monkeypatch, understand, spy, message_count=0)
    prepared = _turn("你知道奶绿波吗", session_id=7, user_id=3)
    assert prepared["early"] is False
    assert prepared["degraded"] is True
    assert prepared["scene"] == "捧哏金句"
    assert spy.calls == []
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]
    # 降级轮仍注入冷启动标记，标记只是文本。
    assert _COLD in prepared["messages"][0]["content"]


def test_case6_timeout_and_empty_do_not_fail_the_turn(monkeypatch):
    timeout_spy = SearchSpy(boom=TimeoutError("timed out"))

    def understand(query, history):
        return _rewrite(needs_web_search=True)

    _install(monkeypatch, understand, timeout_spy)
    prepared = _turn("你知道奶绿波吗")
    assert prepared["early"] is False
    assert prepared["degraded"] is False
    assert "messages" in prepared
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]

    empty_spy = SearchSpy(PluginResult(ok=True, data={"hits": []}))
    _install(monkeypatch, understand, empty_spy)
    prepared = _turn("你知道奶绿波吗")
    assert empty_spy.calls
    assert prepared["degraded"] is False
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]


def test_missing_api_key_is_logged_and_turn_continues(monkeypatch):
    spy = SearchSpy(PluginResult(ok=False, error="未配置 BOCHA_API_KEY"))

    def understand(query, history):
        return _rewrite(needs_web_search=True)

    _install(monkeypatch, understand, spy)
    prepared = _turn("你知道奶绿波吗")
    assert prepared["early"] is False
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]


def test_unregistered_plugin_does_not_fail_the_turn(monkeypatch):
    def understand(query, history):
        return _rewrite(needs_web_search=True)

    _install(monkeypatch, understand, SearchSpy())
    monkeypatch.setattr(agent, "get_plugin", lambda name: None)
    prepared = _turn("你知道奶绿波吗")
    assert prepared["early"] is False
    assert _INJECTED_WEB not in prepared["messages"][0]["content"]


def test_bocha_posts_documented_body_and_parses_web_pages(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "code": 200,
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "奶绿波",
                                    "snippet": "摘要",
                                    "url": "https://example.com/a",
                                    "summary": "这是大模型摘要，本期不用",
                                }
                            ]
                        }
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = BochaProvider("https://api.bochaai.com/v1/web-search", "secret-key", 10)
    hits = provider.search("你知道奶绿波吗", 8)
    assert seen["url"] == "https://api.bochaai.com/v1/web-search"
    assert seen["auth"] == "Bearer secret-key"
    assert seen["timeout"] == 10
    assert seen["body"] == {
        "query": "你知道奶绿波吗",
        "freshness": "noLimit",
        "summary": True,
        "count": 8,
    }
    assert hits[0].title == "奶绿波"
    assert hits[0].snippet == "摘要"
    assert hits[0].url == "https://example.com/a"


def test_bocha_empty_pages_returns_empty_list(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"code":200,"data":{"webPages":{"value":[]}}}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    hits = BochaProvider("https://api.bochaai.com/v1/web-search", "k", 10).search("q", 8)
    assert hits == []


def test_bocha_missing_key_raises_before_http(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    provider = BochaProvider.from_config(
        {"bocha": {"base_url": "https://api.bochaai.com/v1/web-search"}},
        10,
    )
    with pytest.raises(RuntimeError, match="未配置 BOCHA_API_KEY"):
        provider.search("q", 8)


def test_tavily_search_is_not_implemented():
    provider = TavilyProvider.from_config(
        {"tavily": {"base_url": "https://api.tavily.com/search"}},
        10,
    )
    with pytest.raises(NotImplementedError, match="Tavily 留待后续接入"):
        provider.search("q", 8)


def test_web_block_sits_between_script_and_memory_and_cold_start_is_last(monkeypatch):
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={"hits": [{"title": "资料", "snippet": "摘要", "url": "https://e"}]},
        )
    )

    def understand(query, history):
        # 劝谏和树洞会跳过搜索，这两块的顺序放在用例 8～10。
        return _rewrite(scene="安慰话术", needs_web_search=True, recall_query="茶")

    _install(monkeypatch, understand, spy, message_count=0)
    monkeypatch.setattr(agent, "_recall_memory", lambda *args, **kwargs: "主人说过喜欢喝茶")
    prepared = _turn("破防了", session_id=1, user_id=2)
    content = prepared["messages"][0]["content"]
    positions = [
        content.index("# 本轮话术参考"),
        content.index(_INJECTED_WEB),
        content.index("# 旧会话记忆"),
        content.index("# 本轮冷启动"),
    ]
    assert positions == sorted(positions)
    assert content.endswith(_COLD)


def test_cold_start_only_on_first_turn_and_failure_does_not_block(monkeypatch):
    spy = SearchSpy()

    def understand(query, history):
        return _rewrite(needs_web_search=False)

    _install(monkeypatch, understand, spy, message_count=0)
    first = _turn("你好", session_id=1)
    assert _COLD in first["messages"][0]["content"]

    _install(monkeypatch, understand, spy, message_count=2)
    second = _turn("第二句", session_id=1)
    assert _COLD not in second["messages"][0]["content"]

    _install(monkeypatch, understand, spy)
    anonymous = _turn("脚本直调", session_id=None)
    assert _COLD not in anonymous["messages"][0]["content"]

    _install(monkeypatch, understand, spy, count_raises=RuntimeError("db down"))
    broken = _turn("还有历史的会话", session_id=9)
    assert broken["early"] is False
    assert _COLD not in broken["messages"][0]["content"]


def test_case7_daily_venting_does_not_advise_or_go_silent(monkeypatch):
    spy = SearchSpy()

    def understand(query, history):
        return _rewrite(scene="职场吐槽", emotion_alert=False, needs_silence=False)

    _install(monkeypatch, understand, spy)
    prepared = _turn("烦死了，那个同事又找我")
    content = prepared["messages"][0]["content"]
    assert prepared["early"] is False
    assert prepared["degraded"] is False
    assert prepared["emotion_alert"] is False
    assert prepared["scene"] == "职场吐槽"
    assert "话术样本" in content
    assert _ADVICE not in content
    assert _SILENCE not in content
    assert spy.calls == []


def test_case8_crisis_advises_and_skips_search(monkeypatch):
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={"hits": [{"title": "热线", "snippet": "摘要", "url": "https://help.example"}]},
        )
    )

    def understand(query, history):
        return _rewrite(
            scene="安慰话术",
            emotion_alert=True,
            needs_silence=False,
            needs_web_search=True,
            recall_query="茶",
        )

    _install(monkeypatch, understand, spy, message_count=0)
    monkeypatch.setattr(agent, "_recall_memory", lambda *args, **kwargs: "主人说过喜欢喝茶")
    with _info_logs() as lines:
        prepared = _turn("活着真没意思", session_id=3, user_id=2)
    content = prepared["messages"][0]["content"]
    assert prepared["emotion_alert"] is True
    assert prepared["degraded"] is False
    assert spy.calls == []
    assert _ADVICE in content
    assert "找信得过的真人诉说" in content
    assert "可以问主子愿意多说说吗。" in content
    assert _SILENCE not in content
    assert any("reason=emotion_alert" in line for line in lines)
    assert not any("[needs_silence] 判定" in line for line in lines)
    positions = [
        content.index("# 旧会话记忆"),
        content.index(_ADVICE),
        content.index("# 本轮冷启动"),
    ]
    assert positions == sorted(positions)
    assert content.endswith(_COLD)


def test_case9_repeated_dismissal_enters_silence_and_skips_search(monkeypatch):
    history = (
        "主人: 烦死了\n"
        "小喜子: 主子莫气，奴才给您来一段\n"
        "主人: 嗯\n"
        "小喜子: 那奴才再讲一个\n"
        "主人: 哦"
    )
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={"hits": [{"title": "不应出现", "snippet": "摘要", "url": "https://e"}]},
        )
    )
    seen = {}

    def understand(query, history_text):
        seen["history"] = history_text
        silence = "主人: 嗯" in history_text and "主人: 哦" in history_text
        return _rewrite(
            scene="安慰话术",
            emotion_alert=False,
            needs_silence=silence,
            needs_web_search=True,
        )

    _install(monkeypatch, understand, spy)
    monkeypatch.setattr(agent, "get_history_by_session", lambda *args, **kwargs: history)
    with _info_logs() as lines:
        prepared = _turn("知道了", session_id=4, user_id=2)
    content = prepared["messages"][0]["content"]
    assert seen["history"] == history
    assert history in prepared["messages"][1]["content"]
    assert prepared["degraded"] is False
    assert spy.calls == []
    assert _SILENCE in content
    assert _SILENCE_ACT in content
    assert _ADVICE not in content
    assert _INJECTED_WEB not in content
    assert any(
        "[needs_silence] 判定" in line and "needs=true" in line and "emotion_alert=false" in line
        for line in lines
    )
    assert any("跳过" in line and "reason=needs_silence" in line for line in lines)


def test_case10_silence_overrides_advice(monkeypatch):
    spy = SearchSpy(
        PluginResult(
            ok=True,
            data={"hits": [{"title": "不应出现", "snippet": "摘要", "url": "https://e"}]},
        )
    )

    def understand(query, history):
        return _rewrite(
            scene="安慰话术",
            emotion_alert=True,
            needs_silence=True,
            needs_web_search=True,
            recall_query="茶",
        )

    _install(monkeypatch, understand, spy, message_count=0)
    monkeypatch.setattr(agent, "_recall_memory", lambda *args, **kwargs: "主人说过喜欢喝茶")
    with _info_logs() as lines:
        prepared = _turn("活着没意思，别逗了", session_id=5, user_id=2)
    content = prepared["messages"][0]["content"]
    assert prepared["emotion_alert"] is True
    assert spy.calls == []
    assert _SILENCE in content
    assert _SILENCE_ACT in content
    assert _ADVICE not in content
    assert any(
        "[needs_silence] 判定" in line and "needs=true" in line and "emotion_alert=true" in line
        for line in lines
    )
    assert any("跳过" in line and "reason=needs_silence" in line for line in lines)
    assert not any("reason=emotion_alert" in line for line in lines)
    positions = [
        content.index("# 旧会话记忆"),
        content.index(_SILENCE),
        content.index("# 本轮冷启动"),
    ]
    assert positions == sorted(positions)
    assert content.endswith(_COLD)
