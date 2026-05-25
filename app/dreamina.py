import asyncio
import json
import os
import re
import shutil
import tempfile

DREAMINA_BIN = shutil.which("dreamina") or "dreamina"
CLI_TIMEOUT = 120  # seconds — prevents worker hang


async def run_dreamina(*args) -> tuple[str, str, int]:
    """Run a dreamina CLI command with timeout. Returns (stdout, stderr, returncode)."""
    cmd = [DREAMINA_BIN, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT)
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
        raise RuntimeError(f"dreamina command timed out after {CLI_TIMEOUT}s")


def parse_submit_output(output: str) -> dict:
    """Parse dreamina CLI output. Tries JSON first, then text.

    Returns keys: submit_id, gen_status, fail_reason, result_url, compliance_required.
    """
    result = {
        "submit_id": None,
        "gen_status": None,
        "fail_reason": None,
        "result_url": None,
        "compliance_required": False,
    }

    # Check for compliance gate
    if "AigcComplianceConfirmationRequired" in output:
        result["compliance_required"] = True
        result["gen_status"] = "fail"
        result["fail_reason"] = "Compliance confirmation required — please authorize on Dreamina Web first"

    # Try JSON parse
    try:
        data = json.loads(output)
        if isinstance(data, list):
            # For list_task / query_result array, merge all records
            pass  # handled below per-element
        if isinstance(data, dict):
            result["submit_id"] = data.get("submit_id")
            result["gen_status"] = data.get("gen_status")
            result["fail_reason"] = data.get("fail_reason", "") or ""
            # result_url may be nested in result_json
            rj = data.get("result_json", {})
            videos = rj.get("videos", []) if isinstance(rj, dict) else []
            if videos and isinstance(videos, list) and len(videos) > 0:
                result["result_url"] = videos[0].get("video_url", "")
            if data.get("result_url"):
                result["result_url"] = data["result_url"]
            return result
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # Fallback: text parsing
    lines = output.strip().split("\n")
    for line in lines:
        line = line.strip()
        if "submit_id" in line.lower():
            m = re.search(r"[0-9a-fA-F-]{8,64}", line)
            if m:
                result["submit_id"] = m.group(0)
        if "gen_status" in line.lower():
            lower = line.lower()
            if "querying" in lower:
                result["gen_status"] = "querying"
            elif "success" in lower:
                result["gen_status"] = "success"
            elif "fail" in lower:
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
    if references and len(references) > 0:
        return "multimodal2video"
    return "text2video"


def build_submit_command(task_type: str, prompt: str, params: dict,
                         ref_files: list[str]) -> list[str]:
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


async def check_cli_health() -> dict:
    """Check CLI installation and login status."""
    installed = shutil.which("dreamina") is not None
    if not installed:
        return {"ok": False, "cli_installed": False, "login_status": "not_logged_in"}
    try:
        _, _, rc = await run_dreamina("user_credit")
        if rc == 0:
            return {"ok": True, "cli_installed": True, "login_status": "logged_in"}
        return {"ok": False, "cli_installed": True, "login_status": "not_logged_in"}
    except Exception:
        return {"ok": False, "cli_installed": True, "login_status": "error"}
