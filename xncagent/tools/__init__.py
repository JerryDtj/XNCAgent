"""工具插件层：启动时注册一期插件。"""

from xncagent.tools.joke_plugin import joke_plugin
from xncagent.tools.music_plugin import music_plugin
from xncagent.tools.registry import register

register(music_plugin)
register(joke_plugin)
