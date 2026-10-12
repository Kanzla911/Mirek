import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from mirek import agent as agent_mod
from mirek import config, server
from mirek.agent import Agent, history_view, parse_text_tool_calls, visible_text
from mirek.tools import Context, execute


class FakeLLM:
    """Spielt vorbereitete Antworten ab, statt ein echtes Modell zu fragen."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
        self.model = "fake"

    def chat(self, messages, tools=None):
        self.seen.append((messages, tools))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield from reply


def call(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._home = config.HOME
        config.HOME = self.dir / "home"
        self.cfg = dict(config.DEFAULTS, workspace=str(self.dir), permission_mode="ask")

    def tearDown(self):
        config.HOME = self._home
        self.tmp.cleanup()


class ToolTests(Base):
    def ctx(self):
        return Context(self.dir, self.dir / "home", 10)

    def test_write_read_edit(self):
        ctx = self.ctx()
        self.assertTrue(execute(ctx, "write_file", {"path": "a/b.txt", "content": "hallo welt"})[0])
        ok, out = execute(ctx, "edit_file", {"path": "a/b.txt", "old_text": "welt", "new_text": "Mirek"})
        self.assertTrue(ok, out)
        self.assertIn("hallo Mirek", execute(ctx, "read_file", {"path": "a/b.txt"})[1])
        ok, out = execute(ctx, "edit_file", {"path": "a/b.txt", "old_text": "gibtsnicht", "new_text": "x"})
        self.assertFalse(ok)

    def test_command_and_cd(self):
        ctx = self.ctx()
        (self.dir / "sub").mkdir()
        execute(ctx, "change_directory", {"path": "sub"})
        ok, out = execute(ctx, "run_command", {"command": "pwd"})
        self.assertTrue(ok)
        self.assertIn("sub", out)
        self.assertIn("Exit-Code: 0", out)

    def test_search_find_python(self):
        ctx = self.ctx()
        (self.dir / "notiz.md").write_text("Einkauf: Milch\n")
        self.assertIn("notiz.md", execute(ctx, "find_files", {"pattern": "*.md"})[1])
        self.assertIn("notiz.md:1", execute(ctx, "search_text", {"query": "milch"})[1])
        self.assertIn("42", execute(ctx, "run_python", {"code": "print(6*7)"})[1])

    def test_bad_calls(self):
        ctx = self.ctx()
        self.assertFalse(execute(ctx, "zaubern", {})[0])
        ok, out = execute(ctx, "read_file", {"unsinn": 1})
        self.assertFalse(ok)
        self.assertIn("path", out)

    def test_remember(self):
        execute(self.ctx(), "remember", {"fact": "Mag Kaffee schwarz"})
        self.assertIn("Kaffee", (self.dir / "home" / "memory.md").read_text())


class ParsingTests(unittest.TestCase):
    def test_text_tool_calls(self):
        calls = parse_text_tool_calls('Ok.\n<tool>{"name": "list_dir", "arguments": {"path": "."}}</tool>')
        self.assertEqual(calls[0]["function"]["name"], "list_dir")
        calls = parse_text_tool_calls('```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```')
        self.assertEqual(calls[0]["function"]["arguments"], {"path": "x"})
        self.assertEqual(parse_text_tool_calls('{"name": "kein_tool"}'), [])

    def test_visible_text_hides_markup(self):
        self.assertEqual(visible_text("Hallo <tool>{}</tool>"), "Hallo ")
        self.assertEqual(visible_text("Hallo <to"), "Hallo ")
        self.assertEqual(visible_text("a < b"), "a < b")


class AgentTests(Base):
    def test_tool_loop_with_approval(self):
        llm = FakeLLM([
            [call("write_file", path="x.txt", content="42")],
            [{"content": "Fertig"}, {"content": "!"}],
        ])
        a = Agent(self.cfg, llm=llm)
        asked = []
        events = list(a.run("Schreib 42 in x.txt", lambda req: asked.append(req) or True))
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "approval", "tool_result", "token", "token", "done"])
        self.assertEqual((self.dir / "x.txt").read_text(), "42")
        self.assertEqual(asked[0]["name"], "write_file")
        # Ergebnis wurde dem Modell zurückgegeben
        self.assertEqual(llm.seen[1][0][-1]["role"], "tool")
        # und gespeichert
        reloaded = Agent(self.cfg, llm=FakeLLM([]), session_id=a.session_id)
        self.assertEqual(len(reloaded.messages), 4)
        view = history_view(reloaded.messages)
        self.assertEqual([v["type"] for v in view], ["user", "tool", "assistant"])

    def test_denied_and_readonly(self):
        llm = FakeLLM([[call("run_command", command="touch boom")], [{"content": "ok"}]])
        events = list(Agent(self.cfg, llm=llm).run("los", lambda req: False))
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertFalse(result["ok"])
        self.assertFalse((self.dir / "boom").exists())

        self.cfg["permission_mode"] = "readonly"
        llm = FakeLLM([[call("write_file", path="y", content="1")], [{"content": "ok"}]])
        events = list(Agent(self.cfg, llm=llm).run("los", lambda req: self.fail("darf nicht fragen")))
        self.assertNotIn("approval", [e["type"] for e in events])
        self.assertFalse((self.dir / "y").exists())

    def test_safe_tools_need_no_approval(self):
        llm = FakeLLM([[call("list_dir", path=".")], [{"content": "ok"}]])
        events = list(Agent(self.cfg, llm=llm).run("zeig", lambda req: self.fail("darf nicht fragen")))
        self.assertTrue(next(e for e in events if e["type"] == "tool_result")["ok"])

    def test_text_mode_fallback(self):
        llm = FakeLLM([
            agent_mod.OllamaError("registry.ollama.ai/library/gemma:2b does not support tools"),
            [{"content": 'Moment <tool>{"name": "list_dir", "arguments": {}}</tool>'}],
            [{"content": "Erledigt"}],
        ])
        a = Agent(self.cfg, llm=llm)
        events = list(a.run("zeig", lambda req: True))
        self.assertFalse(a.native_tools)
        tokens = "".join(e["text"] for e in events if e["type"] == "token")
        self.assertEqual(tokens, "Moment Erledigt")
        self.assertIsNone(llm.seen[1][1])  # keine nativen Tools mehr
        self.assertIn("<tool>", llm.seen[1][0][0]["content"])  # Anleitung im Systemprompt
        self.assertTrue(llm.seen[2][0][-1]["content"].startswith("[Ergebnis von list_dir]"))

    def test_step_limit(self):
        self.cfg["max_steps"] = 2
        llm = FakeLLM([[call("list_dir")], [call("list_dir")]])
        events = list(Agent(self.cfg, llm=llm).run("x", lambda r: True))
        self.assertIn("Schrittlimit", events[-2]["text"])


class ServerTests(Base):
    def setUp(self):
        super().setUp()
        self.app = server.App(self.cfg)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, path, body=None, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Mirek-Token"] = self.app.token
        data = json.dumps(body).encode() if body is not None else None
        return urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers), timeout=10)

    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.req("/api/sessions", token=False)
        self.assertEqual(e.exception.code, 403)
        page = self.req("/", token=False).read().decode()
        self.assertIn(self.app.token, page)

    def test_chat_stream_with_approval(self):
        a = Agent(self.cfg, llm=FakeLLM([[call("write_file", path="w.txt", content="hi")], [{"content": "Gemacht"}]]),
                  session_id="s1")
        self.app.agents["s1"], self.app.busy["s1"] = a, threading.Lock()
        events = []
        with self.req("/api/chat", {"session": "s1", "text": "schreib"}) as resp:
            for line in resp:
                ev = json.loads(line)
                events.append(ev["type"])
                if ev["type"] == "approval":
                    self.req("/api/approve", {"id": ev["id"], "allow": True}).read()
        self.assertEqual(events, ["tool_call", "approval", "tool_result", "token", "done"])
        self.assertEqual((self.dir / "w.txt").read_text(), "hi")
        sessions = json.load(self.req("/api/sessions"))
        self.assertEqual(sessions[0]["title"], "schreib")
        view = json.load(self.req("/api/session?id=s1"))
        self.assertEqual([i["type"] for i in view["items"]], ["user", "tool", "assistant"])

    def test_status_without_ollama(self):
        self.cfg["ollama_host"] = "http://127.0.0.1:9"
        status = json.load(self.req("/api/status"))
        self.assertFalse(status["ok"])

    def test_settings(self):
        self.cfg["ollama_host"] = "http://127.0.0.1:9"
        json.load(self.req("/api/settings", {"model": "qwen3:4b", "permission_mode": "auto"}))
        saved = json.loads((config.HOME / "config.json").read_text())
        self.assertEqual((saved["model"], saved["permission_mode"]), ("qwen3:4b", "auto"))
        with self.assertRaises(urllib.error.HTTPError):
            self.req("/api/settings", {"workspace": "/gibt/es/nicht"})


if __name__ == "__main__":
    unittest.main()
