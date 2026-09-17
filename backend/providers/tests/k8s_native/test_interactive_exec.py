"""Terminal streams must not consume the executor needed to control them."""

import asyncio
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from k8s_native.observability import InteractiveExecSession


class IdleResponse:
    def __init__(self):
        self.closed = threading.Event()
        self.output = deque()
        self.writes = []
        self.resizes = []

    def is_open(self):
        return not self.closed.is_set()

    def update(self, timeout):
        self.closed.wait(0.005)

    def peek_stdout(self):
        return bool(self.output)

    def read_stdout(self):
        return self.output.popleft()

    def peek_stderr(self):
        return False

    def read_channel(self, channel):
        return '{"status":"Success"}'

    def write_stdin(self, data):
        self.writes.append(data)
        self.output.append(data)

    def write_channel(self, channel, data):
        self.resizes.append((channel, data))

    def close(self):
        self.closed.set()


def test_idle_sessions_leave_capacity_for_input_resize_open_and_close():
    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=4))
        responses = [IdleResponse() for _ in range(4)]
        sessions = [InteractiveExecSession(response) for response in responses]
        readers = [
            asyncio.create_task(reader())
            for session in sessions
            for reader in (session.read_stdout, session.read_stderr, session.wait_exit)
        ]
        try:
            await asyncio.sleep(0.05)
            await asyncio.wait_for(sessions[0].write_stdin("hello"), timeout=1)
            assert await asyncio.wait_for(readers[0], timeout=1) == "hello"
            await asyncio.wait_for(sessions[1].resize(rows=30, cols=100), timeout=1)
            assert responses[1].resizes
            assert await asyncio.wait_for(loop.run_in_executor(None, lambda: "another session"), timeout=1)
            await asyncio.wait_for(asyncio.gather(*(s.close() for s in sessions)), timeout=1)
            assert all(response.closed.is_set() for response in responses)
        finally:
            # Also release blocking SDK reads in the unfixed implementation,
            # so a failing regression cannot hang the test runner's shutdown.
            for response in responses:
                response.close()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    asyncio.run(exercise())


def test_cancelled_reader_does_not_consume_the_next_output():
    async def exercise():
        response = IdleResponse()
        session = InteractiveExecSession(response)
        reader = asyncio.create_task(session.read_stdout())
        try:
            await asyncio.sleep(0.05)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            response.output.append("after cancellation")
            await asyncio.sleep(0.05)
            assert await asyncio.wait_for(session.read_stdout(), timeout=1) == "after cancellation"
        finally:
            response.close()

    asyncio.run(exercise())
