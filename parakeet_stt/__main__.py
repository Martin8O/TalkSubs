"""Entry point. Sets the project environment FIRST, before anything can import torch or onnxruntime."""

from __future__ import annotations

import sys

from parakeet_stt import env


def main() -> int:
    env.apply()
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "app":  # the local app: server + window (no GPU in this process)
        from parakeet_stt.server import launcher

        return launcher.main(sys.argv[2:])
    if command == "worker":  # one transcription job for the app server, spoken to over pipes
        from parakeet_stt.server import worker

        return worker.main()
    if command == "machine":  # what this PC runs the model on (JSON), asked by the app server at its start
        import json

        from parakeet_stt import machine

        print(json.dumps(machine.detect(probe=True).as_dict()))
        return 0
    if command == "dialog":  # Windows' Open dialog for the app server (it prints the chosen paths)
        from parakeet_stt.server import dialogs

        return dialogs.main(sys.argv[2:])
    from parakeet_stt import cli

    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
