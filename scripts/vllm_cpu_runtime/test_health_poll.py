"""Actual localhost startup resets; Docker effects are isolated test doubles."""

import importlib.util
import json
import socket
import struct
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "cpu_runtime_health_smoke", Path(__file__).with_name("smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@contextmanager
def startup_server(*, always_reset=False):
    observations = {"health": 0, "completion": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observations["health"] += 1
            if always_reset or observations["health"] == 1:
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                self.close_connection = True
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            observations["completion"] += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            data = json.dumps(
                {"choices": [{"message": {"content": "synthetic completion"}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, observations
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def docker_observations(port, *, stops_after_reset=False):
    inspections = 0

    def output(command, **_kwargs):
        nonlocal inspections
        if command[:2] == ["docker", "inspect"]:
            inspections += 1
            stopped = stops_after_reset and inspections >= 3
            return json.dumps(
                [
                    {
                        "State": {
                            "Running": not stopped,
                            "ExitCode": 1 if stopped else 0,
                            "OOMKilled": False,
                        },
                        "NetworkSettings": {
                            "Ports": {"8000/tcp": [{"HostPort": str(port)}]}
                        },
                    }
                ]
            ).encode()
        if command[:3] == ["docker", "run", "--rm"]:
            return b'{"vllm":"0.15.1+cpu","float32_cpu_kernel":true}\n'
        if command[:3] == ["docker", "run", "--detach"]:
            return b"owned-test-container"
        raise AssertionError("Unexpected subprocess in isolated smoke proof")

    return output


class HealthPollingTests(unittest.TestCase):
    def run_smoke(self, port, **options):
        return patch.object(
            smoke.subprocess,
            "check_output",
            side_effect=docker_observations(port, **options),
        )

    def test_native_startup_reset_then_healthy_reaches_controlled_completion(self):
        with startup_server() as (
            port,
            observed,
        ), tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.json"
            with self.run_smoke(port), patch.object(
                smoke, "inspect_target", return_value="synthetic-image"
            ), patch.object(smoke.time, "sleep"), patch.object(
                smoke.subprocess, "run"
            ) as remove:
                smoke.smoke("synthetic-image", proof)
            data = json.loads(proof.read_text())
            self.assertTrue(data["passed"])
            self.assertTrue(data["completion_received"])
            self.assertFalse(data["live_cluster_certified"])
            self.assertEqual(observed, {"health": 2, "completion": 1})
            self.assertEqual(
                remove.call_args.args[0],
                ["docker", "rm", "--force", "owned-test-container"],
            )

    def test_repeated_native_resets_still_stop_at_original_deadline(self):
        with startup_server(always_reset=True) as (
            port,
            observed,
        ), tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.json"
            with self.run_smoke(port), patch.object(
                smoke, "inspect_target", return_value="synthetic-image"
            ), patch.object(smoke.time, "sleep"), patch.object(
                smoke.time, "monotonic", side_effect=[0, 1, 901]
            ), patch.object(smoke.subprocess, "run") as remove:
                with self.assertRaises(SystemExit):
                    smoke.smoke("synthetic-image", proof)
            data = json.loads(proof.read_text())
            self.assertFalse(data["passed"])
            self.assertEqual(data["failure_type"], "TimeoutError")
            self.assertNotIn("completion_received", data)
            self.assertEqual(observed, {"health": 1, "completion": 0})
            remove.assert_called_once()

    def test_container_death_after_native_reset_is_not_treated_as_readiness(self):
        with startup_server(always_reset=True) as (
            port,
            observed,
        ), tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / "proof.json"
            with self.run_smoke(port, stops_after_reset=True), patch.object(
                smoke, "inspect_target", return_value="synthetic-image"
            ), patch.object(smoke.time, "sleep"), patch.object(
                smoke.subprocess, "run"
            ) as remove:
                with self.assertRaises(SystemExit):
                    smoke.smoke("synthetic-image", proof)
            data = json.loads(proof.read_text())
            self.assertFalse(data["passed"])
            self.assertEqual(data["failure_type"], "ValueError")
            self.assertEqual(data["container_exit"], 1)
            self.assertFalse(data["oom_killed"])
            self.assertNotIn("completion_received", data)
            self.assertEqual(observed, {"health": 1, "completion": 0})
            remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
