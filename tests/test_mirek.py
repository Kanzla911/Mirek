import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

from mirek import agent as agent_mod
from mirek import config, scheduler, server
from mirek.agent import Agent, history_view, parse_text_tool_calls, visible_text
from mirek.llm import OllamaError, OpenAICompat
from mirek.tools import Context, execute, is_critical


class FakeLLM:
    """Spielt vorbereitete Antworten ab, statt ein echtes Modell zu fragen."""

    def __init__(self, replies, caps=("tools",)):
        self.replies = list(replies)
        self.seen = []
        self.model = "fake"
        self._caps = set(caps)

    def capabilities(self, name=None):
        return self._caps

    def chat(self, messages, tools=None, vision=False):
        self.seen.append({"messages": messages, "tools": tools, "vision": vision})
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

    def ctx(self):
        return Context(self.dir, config.HOME, 10, cfg=self.cfg)


class ToolTests(Base):
    def test_write_read_edit_backup_undo(self):
        c = self.ctx()
        self.assertTrue(execute(c, "write_file", {"path": "a/b.txt", "content": "hallo welt"})[0])
        ok, out, _ = execute(c, "edit_file", {"path": "a/b.txt", "old_text": "welt", "new_text": "Mirek"})
        self.assertTrue(ok, out)
        self.assertIn("hallo Mirek", execute(c, "read_file", {"path": "a/b.txt"})[1])
        # Rückgängig: wieder "welt"
        execute(c, "undo_file", {"path": str(self.dir / "a/b.txt")})
        self.assertIn("hallo welt", execute(c, "read_file", {"path": "a/b.txt"})[1])

    def test_delete_restore(self):
        c = self.ctx()
        (self.dir / "weg.txt").write_text("x")
        execute(c, "delete_path", {"path": "weg.txt"})
        self.assertFalse((self.dir / "weg.txt").exists())
        execute(c, "undo_file", {"path": str(self.dir / "weg.txt")})
        self.assertTrue((self.dir / "weg.txt").exists())

    def test_move_and_copy(self):
        c = self.ctx()
        (self.dir / "f.txt").write_text("1")
        execute(c, "move_path", {"source": "f.txt", "destination": "g.txt"})
        self.assertTrue((self.dir / "g.txt").exists() and not (self.dir / "f.txt").exists())
        execute(c, "copy_path", {"source": "g.txt", "destination": "h.txt"})
        self.assertEqual((self.dir / "h.txt").read_text(), "1")

    def test_command_python_sysinfo(self):
        c = self.ctx()
        (self.dir / "sub").mkdir()
        execute(c, "change_directory", {"path": "sub"})
        ok, out, _ = execute(c, "run_command", {"command": "pwd"})
        self.assertTrue(ok)
        self.assertIn("sub", out)
        self.assertIn("42", execute(c, "run_python", {"code": "print(6*7)"})[1])
        self.assertIn("System:", execute(c, "system_info", {})[1])

    def test_search_find(self):
        c = self.ctx()
        (self.dir / "notiz.md").write_text("Einkauf: Milch\n")
        self.assertIn("notiz.md", execute(c, "find_files", {"pattern": "*.md"})[1])
        self.assertIn("notiz.md:1", execute(c, "search_text", {"query": "milch"})[1])

    def test_background_process(self):
        c = self.ctx()
        ok, out, _ = execute(c, "process", {"action": "start", "command": "echo hallo-prozess"})
        self.assertTrue(ok, out)
        self.assertIn("hallo-prozess", out + execute(c, "process", {"action": "list"})[1])

    def test_memory_remember_forget(self):
        c = self.ctx()
        execute(c, "remember", {"fact": "Mag Kaffee schwarz"})
        self.assertIn("Kaffee", (config.HOME / "memory.md").read_text())
        execute(c, "forget", {"text": "Kaffee"})
        self.assertNotIn("Kaffee", (config.HOME / "memory.md").read_text())

    def test_plan_emits_event(self):
        c = self.ctx()
        execute(c, "update_plan", {"steps": [{"text": "A", "status": "done"}, "B"]})
        plan = [e for e in c.events if e["type"] == "plan"][0]
        self.assertEqual(plan["steps"][0]["status"], "erledigt")
        self.assertEqual(plan["steps"][1]["status"], "offen")

    def test_critical_detection(self):
        self.assertTrue(is_critical("run_command", {"command": "sudo rm -rf /"}))
        self.assertTrue(is_critical("run_command", {"command": "shutdown now"}))
        self.assertFalse(is_critical("run_command", {"command": "rm -rf build/"}))

    def test_bad_calls(self):
        c = self.ctx()
        self.assertFalse(execute(c, "zaubern", {})[0])
        ok, out, _ = execute(c, "read_file", {"unsinn": 1})
        self.assertFalse(ok)
        self.assertIn("path", out)

    def test_docx_extraction(self):
        path = self.dir / "doc.docx"
        body = '<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>Hallo Welt</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", body)
        self.assertIn("Hallo Welt", execute(self.ctx(), "read_file", {"path": "doc.docx"})[1])


class ParsingTests(unittest.TestCase):
    def test_text_tool_calls(self):
        calls = parse_text_tool_calls('Ok.\n<tool>{"name": "list_dir", "arguments": {"path": "."}}</tool>')
        self.assertEqual(calls[0]["function"]["name"], "list_dir")
        calls = parse_text_tool_calls('```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```')
        self.assertEqual(calls[0]["function"]["arguments"], {"path": "x"})
        self.assertEqual(parse_text_tool_calls('{"name": "kein_tool"}'), [])

    def test_visible_text(self):
        self.assertEqual(visible_text("Hallo <tool>{}</tool>"), "Hallo ")
        self.assertEqual(visible_text("Hallo <to"), "Hallo ")
        self.assertEqual(visible_text("a < b"), "a < b")


class LLMConvertTests(unittest.TestCase):
    def test_openai_tool_roundtrip(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "list_dir", "arguments": {"path": "."}}}]},
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
        ]
        out = OpenAICompat.convert(messages, vision=False)
        self.assertEqual(out[1]["tool_calls"][0]["function"]["name"], "list_dir")
        self.assertEqual(out[2], {"role": "tool", "tool_call_id": "c1", "content": "ok"})


class AgentTests(Base):
    def test_tool_loop_with_approval(self):
        llm = FakeLLM([[call("write_file", path="x.txt", content="42")], [{"content": "Fertig"}]])
        a = Agent(self.cfg, llm=llm)
        asked = []
        events = list(a.run("Schreib 42", lambda req: asked.append(req) or True))
        types = [e["type"] for e in events]
        self.assertEqual(types, ["tool_call", "approval", "tool_result", "token", "done"])
        self.assertEqual((self.dir / "x.txt").read_text(), "42")
        self.assertEqual(llm.seen[1]["messages"][-1]["role"], "tool")
        view = history_view(a.messages)
        self.assertEqual([v["type"] for v in view], ["user", "tool", "assistant"])

    def test_always_allow(self):
        llm = FakeLLM([[call("write_file", path="a", content="1")], [call("write_file", path="b", content="2")], [{"content": "ok"}]])
        a = Agent(self.cfg, llm=llm)
        approvals = []
        list(a.run("zwei dateien", lambda req: (approvals.append(req), "always")[1]))
        self.assertEqual(len(approvals), 1)  # zweites Schreiben nicht mehr gefragt
        self.assertTrue((self.dir / "a").exists() and (self.dir / "b").exists())

    def test_critical_always_asks_in_auto(self):
        self.cfg["permission_mode"] = "auto"
        llm = FakeLLM([[call("run_command", command="shutdown -h now")], [{"content": "ok"}]])
        asked = []
        list(Agent(self.cfg, llm=llm).run("x", lambda req: asked.append(req) or False))
        self.assertEqual(len(asked), 1)
        self.assertTrue(asked[0]["critical"])

    def test_denied_and_readonly(self):
        llm = FakeLLM([[call("run_command", command="touch boom")], [{"content": "ok"}]])
        events = list(Agent(self.cfg, llm=llm).run("los", lambda req: False))
        self.assertFalse(next(e for e in events if e["type"] == "tool_result")["ok"])
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

    def test_vision_detect_and_pass(self):
        llm = FakeLLM([[{"content": "schönes Bild"}]], caps=("tools", "vision"))
        a = Agent(self.cfg, llm=llm)
        (self.dir / "p.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        list(a.run("was ist das?", lambda r: True, images=[str(self.dir / "p.png")]))
        self.assertTrue(llm.seen[0]["vision"])
        self.assertEqual(a.messages[0]["image_paths"], [str(self.dir / "p.png")])

    def test_text_mode_fallback(self):
        llm = FakeLLM([
            OllamaError("registry/gemma:2b does not support tools"),
            [{"content": 'Moment <tool>{"name": "list_dir", "arguments": {}}</tool>'}],
            [{"content": "Erledigt"}],
        ])
        a = Agent(self.cfg, llm=llm)
        events = list(a.run("zeig", lambda req: True))
        self.assertFalse(a.native_tools)
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "token"), "Moment Erledigt")
        self.assertIsNone(llm.seen[1]["tools"])
        self.assertTrue(llm.seen[2]["messages"][-1]["content"].startswith("[Ergebnis von list_dir]"))

    def test_plan_event_forwarded(self):
        llm = FakeLLM([[call("update_plan", steps=[{"text": "A", "status": "läuft"}])], [{"content": "ok"}]])
        events = list(Agent(self.cfg, llm=llm).run("plan", lambda r: True))
        plan = next(e for e in events if e["type"] == "plan")
        self.assertEqual(plan["steps"][0]["status"], "läuft")


class SchedulerTests(Base):
    def test_parse_time(self):
        from datetime import datetime

        now = datetime(2026, 10, 12, 10, 0)
        self.assertEqual(scheduler.parse_time("14:30", now).hour, 14)
        self.assertEqual(scheduler.parse_time("morgen 8:00", now).day, 13)
        self.assertEqual(scheduler.parse_time("09:00", now).day, 13)  # schon vorbei → morgen

    def test_add_list_remove(self):
        task = scheduler.add_task("Wasser trinken", in_minutes=5)
        self.assertIn(task, scheduler.load_tasks())
        self.assertTrue(scheduler.remove_task(task["id"]))
        self.assertEqual(scheduler.load_tasks(), [])

    def test_due_task_runs(self):
        import time as _t

        self.cfg["model"] = "fake"
        task = scheduler.add_task("sag hallo", in_minutes=0)
        scheduler.save_tasks([{**task, "next_run": _t.time() - 1}])
        sched = scheduler.Scheduler(self.cfg)
        results = []
        agent_mod_orig = agent_mod.Agent
        try:
            agent_mod.Agent = lambda cfg, session_id=None: _FakeAgent(results)
            threads = sched.tick()
            for th in threads:
                th.join(5)
        finally:
            agent_mod.Agent = agent_mod_orig
        self.assertTrue(results)  # Agent wurde ausgeführt


class _FakeAgent:
    def __init__(self, results):
        self.title = ""
        self.results = results

    def run(self, prompt, approve, images=None):
        self.results.append(prompt)
        yield {"type": "token", "text": "hallo"}


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
        self.assertIn(self.app.token, self.req("/", token=False).read().decode())

    def test_chat_stream_with_approval(self):
        a = Agent(self.cfg, llm=FakeLLM([[call("write_file", path="w.txt", content="hi")], [{"content": "Gemacht"}]]), session_id="s1")
        self.app.agents["s1"], self.app.busy["s1"] = a, threading.Lock()
        events = []
        with self.req("/api/chat", {"session": "s1", "text": "schreib"}) as resp:
            for line in resp:
                if not line.strip():
                    continue
                ev = json.loads(line)
                events.append(ev["type"])
                if ev["type"] == "approval":
                    self.req("/api/approve", {"id": ev["id"], "decision": True}).read()
        self.assertEqual(events, ["tool_call", "approval", "tool_result", "token", "done"])
        self.assertEqual((self.dir / "w.txt").read_text(), "hi")
        self.assertEqual(json.load(self.req("/api/sessions"))[0]["title"], "schreib")

    def test_status_and_settings(self):
        self.cfg["ollama_host"] = "http://127.0.0.1:9"
        self.assertFalse(json.load(self.req("/api/status"))["ok"])
        json.load(self.req("/api/settings", {"model": "qwen3:4b", "permission_mode": "auto", "backend": "ollama"}))
        saved = json.loads((config.HOME / "config.json").read_text())
        self.assertEqual((saved["model"], saved["permission_mode"]), ("qwen3:4b", "auto"))
        with self.assertRaises(urllib.error.HTTPError):
            self.req("/api/settings", {"workspace": "/gibt/es/nicht"})

    def test_upload_and_image_guard(self):
        r = urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/upload", data=b"\x89PNG\r\n\x1a\n",
            headers={"X-Mirek-Token": self.app.token, "X-Filename": "foto.png"}), timeout=10)
        data = json.load(r)
        self.assertTrue(data["image"])
        # Bild darf geladen werden, /etc/passwd nicht
        self.assertEqual(self.req("/api/image?path=" + data["path"]).status, 200)
        with self.assertRaises(urllib.error.HTTPError):
            self.req("/api/image?path=/etc/passwd")

    def test_tasks_api(self):
        scheduler.add_task("test", in_minutes=10)
        tasks = json.load(self.req("/api/tasks"))
        self.assertEqual(len(tasks), 1)
        self.req("/api/task/delete", {"id": tasks[0]["id"]}).read()
        self.assertEqual(json.load(self.req("/api/tasks")), [])


if __name__ == "__main__":
    unittest.main()
