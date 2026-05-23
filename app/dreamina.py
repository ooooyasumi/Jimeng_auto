import asyncio
import json
import re
import shlex
import shutil
import tempfile
import os
from typing import Optional

DREAMINA_BIN = shutil.which("dreamina") or "dreamina"


async def run_dreamina(*args) -> tuple[str, str, int]:
    """Run a dreamina CLI command. Returns (stdout, stderr, returncode)."""
    cmd = [DREAMINA_BIN, *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


def parse_submit_output(output: str) -> dict:
    """Parse submit command output to extract submit_id, gen_status, etc."""
    result = {
        "submit_id": None,
        "gen_status": None,
        "fail_reason": None,
        "result_url": None,
    }
    # Try JSON first (list_task format)
    try:
        data = json.loads(output)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        if isinstance(data, dict):
            result["submit_id"] = data.get("submit_id")
            result["gen_status"] = data.get("gen_status")
            result["fail_reason"] = data.get("fail_reason")
            # result_url may be nested
            if data.get("result_url"):
                result["result_url"] = data["result_url"]
            return result
    except (json.JSONDecodeError, KeyError):
        pass

    # Fallback: parse plain text
    lines = output.strip().split("\n")
    for line in lines:
        line = line.strip()
        if "submit_id" in line.lower():
            m = re.search(r"[0-9a-fA-F-]{8,64}", line)
            if m:
                result["submit_id"] = m.group(0)
        if "gen_status" in line.lower():
            if "querying" in line.lower():
                result["gen_status"] = "querying"
            elif "success" in line.lower():
                result["gen_status"] = "success"
            elif "fail" in line.lower():
                result["gen_status"] = "fail"
        if "fail_reason" in line.lower():
            result["fail_reason"] = line.split("fail_reason", 1)[-1].strip(": =\"")
        if "result_url" in line.lower() or "video_url" in line.lower():
            m = re.search(r"(https?://\S+)", line)
            if m:
                result["result_url"] = m.group(1)
    return result


async def list_all_tasks() -> list[dict]:
    """Fetch all recent tasks from dreamina via list_task."""
    stdout, _, _ = await run_dreamina("list_task", "--limit", "50")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return []


def determine_task_type(references: list) -> str:
    """Auto-detect: text2video if no references, multimodal2video otherwise."""
    if references and len(references) > 0:
        return "multimodal2video"
    return "text2video"


def build_submit_command(task_type: str, prompt: str, params: dict,
                         ref_files: list[str]) -> list[str]:
    """Build the dreamina CLI command for submitting a task."""
    if task_type == "text2video":
        cmd = [
            "text2video",
            "--prompt", prompt,
            "--duration", str(params.get("duration", 5)),
            "--ratio", params.get("ratio", "16:9"),
            "--model_version", params.get("model_version", "seedance2.0fast"),
        ]
    else:
        cmd = ["multimodal2video", "--prompt", prompt]
        cmd += ["--duration", str(params.get("duration", 5))]
        cmd += ["--ratio", params.get("ratio", "16:9")]
        cmd += ["--model_version", params.get("model_version", "seedance2.0fast")]
        for ref in ref_files:
            ext = os.path.splitext(ref)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                cmd += ["--image", ref]
            elif ext in (".mp4", ".mov", ".webm", ".avi"):
                cmd += ["--video", ref]
            elif ext in (".mp3", ".wav", ".aac", ".m4a", ".ogg"):
                cmd += ["--audio", ref]
    return cmd
