import argparse
import os
import subprocess
import sys
from pathlib import Path


PYTHON_TARGETS = [
    "api",
    "core",
    "scripts",
    "tests",
]
EXPECTED_PYTHON = (3, 11)
LIGHTWEIGHT_PYTEST_TARGETS = [
    "tests/test_config.py",
    "tests/test_llm_generation_controls.py",
    "tests/test_outbound_http.py",
    "tests/test_tool_ssrf.py",
    "tests/test_probe_models.py",
    "tests/test_anthropic_protocol.py",
    "tests/test_mcp_client.py",
    "tests/test_mysql_trigger_migration.py",
    "tests/test_mcp_sdk_client.py",
    "tests/test_mcp_registry.py",
    "tests/test_skills_dynamic.py",
    "tests/test_conversation_context.py",
    "tests/test_skills_api.py",
    "tests/test_agent_search_config.py",
    "tests/test_mcp_api.py",
    "tests/test_mcp_oauth.py",
    "tests/test_mcp_integration.py",
    "tests/test_query_understanding.py",
    "tests/test_rag_eval.py",
    "tests/test_corpus_tools.py",
    "tests/test_vector_store.py",
    "tests/test_langchain_provider.py",
    "tests/test_ingest_langchain_loaders.py",
]


def run_step(name: str, command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> int:
    location = cwd or Path.cwd()
    print(f"\n==> {name}")
    print(f"cwd: {location}")
    print(f"cmd: {' '.join(command)}")
    completed = subprocess.run(command, cwd=location, env=env)
    if completed.returncode:
        print(f"\n{name} failed with exit code {completed.returncode}.")
    return completed.returncode


def has_command(command: str) -> bool:
    check = "where" if os.name == "nt" else "which"
    return subprocess.run(
        [check, command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def check_python_version() -> int:
    current = sys.version_info[:2]
    if current != EXPECTED_PYTHON:
        expected = ".".join(str(part) for part in EXPECTED_PYTHON)
        actual = ".".join(str(part) for part in current)
        print(f"Python {expected} is required for release checks; current interpreter is Python {actual}.")
        print("Use the project uv environment, for example: uv run python scripts/release_check.py --with-frontend")
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run Lingshu Agent release checks.")
    parser.add_argument("--with-frontend", action="store_true", help="run frontend npm build")
    parser.add_argument("--with-rag-eval", action="store_true", help="run RAG eval; uses mock mode unless --rag-eval-corpus is provided")
    parser.add_argument("--rag-eval-cases", default="eval/rag_cases.jsonl", help="RAG eval cases JSONL path")
    parser.add_argument("--rag-eval-corpus", default=os.getenv("RAG_EVAL_CORPUS", ""), help="optional normalized corpus JSONL path for offline RAG eval")
    parser.add_argument("--skip-pytest", action="store_true", help="skip pytest for faster local iteration")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    python = sys.executable
    steps: list[tuple[str, list[str], Path | None]] = []

    version_error = check_python_version()
    if version_error:
        return version_error

    if not args.skip_pytest:
        if os.getenv("TEST_DATABASE_URL"):
            steps.append(("pytest", [python, "-m", "pytest"], root))
        else:
            print("TEST_DATABASE_URL is not set; running lightweight non-DB pytest targets.")
            steps.append(("pytest lightweight", [python, "-m", "pytest", *LIGHTWEIGHT_PYTEST_TARGETS, "-q", "--timeout=60"], root))
    steps.extend(
        [
            ("compileall", [python, "-m", "compileall", *PYTHON_TARGETS], root),
            ("markdown links", [python, "scripts/check_markdown_links.py"], root),
            ("text encoding", [python, "scripts/check_text_encoding.py"], root),
        ]
    )

    if args.with_rag_eval:
        rag_eval_command = [python, "eval/run_rag_eval.py", "--cases", args.rag_eval_cases, "--summary-only"]
        if args.rag_eval_corpus:
            rag_eval_command.extend(["--corpus", args.rag_eval_corpus])
        else:
            rag_eval_command.append("--mock")
        steps.append(("rag eval", rag_eval_command, root))

    if args.with_frontend:
        npm = npm_command()
        if not has_command(npm):
            print("npm is required for --with-frontend.")
            return 1
        steps.append(("frontend build", [npm, "run", "build"], root / "frontend"))

    for name, command, cwd in steps:
        returncode = run_step(name, command, cwd=cwd)
        if returncode:
            return returncode

    print("\nRelease checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

