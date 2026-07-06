from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import settings


PROVIDER_CONFIG_PATH = Path(os.getenv("LLAMA_CPP_PROVIDER_CONFIG", "config/llama_cpp.json"))


DEFAULT_PROVIDER_SETTINGS: Dict[str, Any] = {
    "ssh_enabled": False,
    "ssh_host": "",
    "ssh_user": "",
    "ssh_port": 22,
    "ssh_key": "",
    "ssh_strict_host_key": False,
    "ssh_timeout": 20,
    "model_dir": "/models/llama",
    "binary_path": "/usr/local/bin/llama-server",
    "runtime_dir": "/tmp/llm-agent-llama",
    "cache_dir": "/models/llama/.llm-agent-cache",
    "default_host": "0.0.0.0",
    "default_port": 8081,
    "readiness_timeout": 180,
    "hf_token": "",
}


RUNTIME_DEFAULTS: Dict[str, Any] = {
    "ctx_size": 0,
    "n_gpu_layers": None,
    "main_gpu": None,
    "tensor_split": "",
    "split_mode": "",
    "flash_attn": False,
    "threads": None,
    "threads_batch": None,
    "batch_size": None,
    "ubatch_size": None,
    "parallel": None,
    "cont_batching": True,
    "cache_enabled": False,
    "cache_path": "",
    "cache_mode": "rw",
    "cache_ram": 8192,
    "cache_idle_slots": True,
    "cache_reuse": 0,
    "ctx_checkpoints": 32,
    "checkpoint_min_step": 256,
    "extra_args": [],
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on", "checked"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(value)


def _default_settings() -> Dict[str, Any]:
    result = dict(DEFAULT_PROVIDER_SETTINGS)
    result["ssh_enabled"] = bool(getattr(settings, "LLM_SERVER_SSH_ENABLED", False))
    result["ssh_host"] = str(getattr(settings, "LLM_SERVER_SSH_HOST", settings.LLM_HOST) or settings.LLM_HOST)
    result["ssh_user"] = str(getattr(settings, "LLM_SERVER_SSH_USER", "") or "")
    result["ssh_port"] = int(getattr(settings, "LLM_SERVER_SSH_PORT", 22) or 22)
    result["ssh_key"] = str(getattr(settings, "LLM_SERVER_SSH_KEY", "") or "")
    result["ssh_strict_host_key"] = bool(getattr(settings, "LLM_SERVER_SSH_STRICT_HOST_KEY", False))
    result["ssh_timeout"] = int(getattr(settings, "LLM_SERVER_SSH_TIMEOUT", 20) or 20)
    return result


def _load_store() -> Dict[str, Any]:
    if not PROVIDER_CONFIG_PATH.exists():
        return {"settings": _default_settings(), "profiles": [], "active_profile_id": None, "download_job": None}
    try:
        with PROVIDER_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    loaded_settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    merged_settings = _default_settings()
    merged_settings.update(loaded_settings)
    profiles = data.get("profiles") if isinstance(data.get("profiles"), list) else []
    return {
        "settings": merged_settings,
        "profiles": [p for p in profiles if isinstance(p, dict)],
        "active_profile_id": data.get("active_profile_id"),
        "download_job": data.get("download_job") if isinstance(data.get("download_job"), dict) else None,
    }


def _write_store(store: Dict[str, Any]) -> None:
    PROVIDER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROVIDER_CONFIG_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2, ensure_ascii=False)
    tmp.replace(PROVIDER_CONFIG_PATH)


def get_provider_settings() -> Dict[str, Any]:
    return _load_store()["settings"]


def get_provider_settings_safe() -> Dict[str, Any]:
    values = dict(get_provider_settings())
    values["ssh_key_configured"] = bool(values.get("ssh_key"))
    if values.get("ssh_key"):
        values["ssh_key"] = ""
    values["hf_token_configured"] = bool(values.get("hf_token"))
    if values.get("hf_token"):
        values["hf_token"] = ""
    return values


def update_provider_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    store = _load_store()
    current = dict(store["settings"])
    allowed = set(DEFAULT_PROVIDER_SETTINGS)
    for key, value in values.items():
        if key not in allowed:
            continue
        if key == "ssh_key" and (value is None or str(value).strip() == "") and current.get("ssh_key"):
            continue
        if key == "hf_token" and (value is None or str(value).strip() == "") and current.get("hf_token"):
            continue
        if key in {"ssh_enabled", "ssh_strict_host_key"}:
            current[key] = bool(value)
        elif key in {"ssh_port", "ssh_timeout", "default_port", "readiness_timeout"}:
            current[key] = int(value)
        else:
            current[key] = "" if value is None else str(value)
    if not current.get("cache_dir"):
        current["cache_dir"] = f"{current.get('model_dir', '/models/llama').rstrip('/')}/.llm-agent-cache"
    store["settings"] = current
    _write_store(store)
    return get_provider_settings_safe()


def _ssh_base_command(provider_settings: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = provider_settings or get_provider_settings()
    if not cfg.get("ssh_enabled"):
        raise RuntimeError("llm-server SSH is disabled")
    if not cfg.get("ssh_host") or not cfg.get("ssh_user"):
        raise RuntimeError("LLM server SSH host/user is not configured")
    cmd = [
        "ssh",
        "-p",
        str(cfg.get("ssh_port") or 22),
        "-o",
        f"ConnectTimeout={int(cfg.get('ssh_timeout') or 20)}",
        "-o",
        "BatchMode=yes",
    ]
    if cfg.get("ssh_key"):
        cmd.extend(["-i", str(cfg["ssh_key"])])
    if not cfg.get("ssh_strict_host_key"):
        cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
    cmd.append(f"{cfg['ssh_user']}@{cfg['ssh_host']}")
    return cmd


def run_ssh(remote_cmd: str, timeout: Optional[int] = None) -> Tuple[bool, str]:
    cfg = get_provider_settings()
    cmd = _ssh_base_command(cfg)
    cmd.append(remote_cmd)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or int(cfg.get("ssh_timeout") or 20),
        )
    except Exception as exc:
        return False, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def test_ssh_connection() -> Dict[str, Any]:
    ok, output = run_ssh("printf llm-agent-ssh-ok")
    return {"ok": ok, "message": output or ("OK" if ok else "SSH failed")}


def parse_artifact_scan(output: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in (output or "").splitlines():
        if not line.strip():
            continue
        parts = line.rsplit("\t", 1)
        if len(parts) != 2:
            continue
        path, size_raw = parts
        try:
            size = int(size_raw)
        except Exception:
            size = None
        rows.append(
            {
                "path": path,
                "name": Path(path).name,
                "size": size,
            }
        )
    rows.sort(key=lambda item: item["path"].lower())
    return rows


def scan_artifacts() -> List[Dict[str, Any]]:
    cfg = get_provider_settings()
    model_dir = str(cfg.get("model_dir") or "/models/llama")
    remote = (
        "test -d {dir} && find {dir} -type f -iname '*.gguf' -printf '%p\\t%s\\n' "
        "|| true"
    ).format(dir=shlex.quote(model_dir))
    ok, output = run_ssh(remote, timeout=max(int(cfg.get("ssh_timeout") or 20), 60))
    if not ok:
        raise RuntimeError(output or "GGUF scan failed")
    return parse_artifact_scan(output)


def validate_hf_repo_id(repo_id: str) -> str:
    repo_id = str(repo_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", repo_id):
        raise ValueError("Hugging Face repo must be formatted as owner/repo")
    return repo_id


def validate_hf_filename(filename: str) -> str:
    filename = str(filename or "").strip()
    if not filename or filename.startswith("/") or "\\" in filename:
        raise ValueError("filename must be a relative GGUF path")
    parts = filename.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("filename must not contain empty, current, or parent path segments")
    if not filename.lower().endswith(".gguf"):
        raise ValueError("filename must end with .gguf")
    return filename


def hf_download_target_dir(repo_id: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    repo_id = validate_hf_repo_id(repo_id)
    provider_settings = cfg or get_provider_settings()
    repo_name = repo_id.split("/", 1)[1]
    return f"{str(provider_settings.get('model_dir') or '/models/llama').rstrip('/')}/{repo_name}"


def hf_download_target_path(repo_id: str, filename: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    filename = validate_hf_filename(filename)
    return f"{hf_download_target_dir(repo_id, cfg).rstrip('/')}/{filename}"


def _download_pid_path(cfg: Optional[Dict[str, Any]] = None) -> str:
    provider_settings = cfg or get_provider_settings()
    return f"{str(provider_settings.get('runtime_dir')).rstrip('/')}/hf-download.pid"


def _download_log_path(cfg: Optional[Dict[str, Any]] = None) -> str:
    provider_settings = cfg or get_provider_settings()
    return f"{str(provider_settings.get('runtime_dir')).rstrip('/')}/hf-download.log"


def build_hf_download_command(repo_id: str, filename: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    provider_settings = cfg or get_provider_settings()
    repo_id = validate_hf_repo_id(repo_id)
    filename = validate_hf_filename(filename)
    target_dir = hf_download_target_dir(repo_id, provider_settings)
    target_path = hf_download_target_path(repo_id, filename, provider_settings)
    runtime_dir = str(provider_settings.get("runtime_dir")).rstrip("/")
    log_file = _download_log_path(provider_settings)
    pid_file = _download_pid_path(provider_settings)
    env_prefix = ""
    if provider_settings.get("hf_token"):
        env_prefix = f"HF_TOKEN={shlex.quote(str(provider_settings['hf_token']))} "
    command = (
        f"{env_prefix}\"$hf_cmd\" download "
        f"{shlex.quote(repo_id)} {shlex.quote(filename)} "
        f"--local-dir {shlex.quote(target_dir)}"
    )
    remote = (
        "mkdir -p {runtime_dir} {target_dir} || exit 10; "
        "hf_cmd=$(command -v hf || command -v huggingface-cli || true); "
        "test -n \"$hf_cmd\" || exit 11; "
        "export hf_cmd; "
        "test -w {target_dir} || exit 12; "
        "nohup sh -c {command} > {log} 2>&1 & echo $! > {pid}; cat {pid}"
    ).format(
        runtime_dir=shlex.quote(runtime_dir),
        target_dir=shlex.quote(target_dir),
        command=shlex.quote(command),
        log=shlex.quote(log_file),
        pid=shlex.quote(pid_file),
    )
    return {
        "command": command,
        "remote": remote,
        "target_dir": target_dir,
        "target_path": target_path,
        "pid_path": pid_file,
        "log_path": log_file,
    }


def _is_download_job_active(job: Optional[Dict[str, Any]]) -> bool:
    return bool(job and job.get("status") in ("starting", "running"))


def get_download_job() -> Optional[Dict[str, Any]]:
    job = _load_store().get("download_job")
    if not job:
        return None
    return refresh_download_job_status(job)


def refresh_download_job_status(job: Dict[str, Any]) -> Dict[str, Any]:
    pid_file = str(job.get("pid_path") or "")
    target_path = str(job.get("target_path") or "")
    if not pid_file:
        return job
    remote = (
        "pidfile={pid}; target={target}; "
        "if [ -f \"$pidfile\" ]; then "
        "pid=$(cat \"$pidfile\" 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then echo running; exit 0; fi; "
        "fi; "
        "if [ -f \"$target\" ]; then echo completed; else echo failed; fi"
    ).format(pid=shlex.quote(pid_file), target=shlex.quote(target_path))
    ok, output = run_ssh(remote)
    status = str(job.get("status") or "unknown")
    if ok:
        if "running" in output:
            status = "running"
        elif "completed" in output:
            status = "completed"
        elif "failed" in output and status in ("starting", "running"):
            status = "error"
    refreshed = dict(job)
    refreshed["status"] = status
    if status in ("completed", "error", "cancelled") and not refreshed.get("completed_at"):
        refreshed["completed_at"] = time.time()
    store = _load_store()
    store["download_job"] = refreshed
    _write_store(store)
    return refreshed


def start_hf_download(repo_id: str, filename: str) -> Dict[str, Any]:
    store = _load_store()
    current = store.get("download_job")
    if _is_download_job_active(refresh_download_job_status(current) if current else None):
        raise RuntimeError("a GGUF download is already active")
    cfg = store["settings"]
    command_info = build_hf_download_command(repo_id, filename, cfg)
    job = {
        "id": uuid.uuid4().hex[:12],
        "repo_id": validate_hf_repo_id(repo_id),
        "filename": validate_hf_filename(filename),
        "target_dir": command_info["target_dir"],
        "target_path": command_info["target_path"],
        "pid_path": command_info["pid_path"],
        "log_path": command_info["log_path"],
        "status": "starting",
        "started_at": time.time(),
    }
    store["download_job"] = job
    _write_store(store)
    ok, output = run_ssh(command_info["remote"], timeout=max(int(cfg.get("ssh_timeout") or 20), 30))
    if not ok:
        job["status"] = "error"
        job["last_error"] = output or "download start failed"
        job["completed_at"] = time.time()
        store = _load_store()
        store["download_job"] = job
        _write_store(store)
        raise RuntimeError(job["last_error"])
    job["status"] = "running"
    job["last_pid"] = output.splitlines()[-1] if output else ""
    store = _load_store()
    store["download_job"] = job
    _write_store(store)
    return job


def cancel_hf_download() -> Optional[Dict[str, Any]]:
    job = _load_store().get("download_job")
    if not job:
        return None
    pid_file = str(job.get("pid_path") or "")
    remote = (
        "pidfile={pid}; "
        "if [ -f \"$pidfile\" ]; then "
        "pid=$(cat \"$pidfile\" 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        "kill \"$pid\"; sleep 1; kill -0 \"$pid\" 2>/dev/null && kill -9 \"$pid\" 2>/dev/null || true; "
        "fi; rm -f \"$pidfile\"; fi; echo cancelled"
    ).format(pid=shlex.quote(pid_file))
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "download cancel failed")
    job = dict(job)
    job["status"] = "cancelled"
    job["completed_at"] = time.time()
    store = _load_store()
    store["download_job"] = job
    _write_store(store)
    return job


def get_download_logs(lines: int = 200) -> str:
    job = _load_store().get("download_job")
    if not job:
        return ""
    remote = "test -f {log} && tail -n {lines} {log} || true".format(
        log=shlex.quote(str(job.get("log_path") or "")),
        lines=int(lines),
    )
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "download log read failed")
    return output


def _profile_runtime(values: Dict[str, Any]) -> Dict[str, Any]:
    runtime = dict(RUNTIME_DEFAULTS)
    for key in runtime:
        if key in values:
            runtime[key] = values[key]
    if isinstance(runtime.get("extra_args"), str):
        runtime["extra_args"] = shlex.split(runtime["extra_args"])
    if not isinstance(runtime.get("extra_args"), list):
        runtime["extra_args"] = []
    for key in [
        "ctx_size",
        "n_gpu_layers",
        "main_gpu",
        "threads",
        "threads_batch",
        "batch_size",
        "ubatch_size",
        "parallel",
        "cache_ram",
        "cache_reuse",
        "ctx_checkpoints",
        "checkpoint_min_step",
    ]:
        value = runtime.get(key)
        if value in ("", None):
            runtime[key] = None if key != "ctx_size" else 0
        else:
            runtime[key] = int(value)
    runtime["flash_attn"] = _coerce_bool(runtime.get("flash_attn"))
    runtime["cont_batching"] = _coerce_bool(runtime.get("cont_batching"))
    runtime["cache_enabled"] = _coerce_bool(runtime.get("cache_enabled"))
    runtime["cache_idle_slots"] = _coerce_bool(runtime.get("cache_idle_slots"))
    return runtime


def _validate_model_path(path: str, cfg: Dict[str, Any]) -> None:
    model_dir = str(cfg.get("model_dir") or "/models/llama").rstrip("/") + "/"
    if not path.startswith(model_dir):
        raise ValueError(f"GGUF path must be under {model_dir.rstrip('/')}")
    if not path.lower().endswith(".gguf"):
        raise ValueError("GGUF path must end with .gguf")


def delete_artifact(path: str) -> Dict[str, Any]:
    cfg = get_provider_settings()
    path = str(path or "").strip()
    _validate_model_path(path, cfg)
    remote = "test -f {path} && rm -- {path}".format(path=shlex.quote(path))
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "GGUF delete failed")
    return {"path": path, "deleted": True}


def list_profiles() -> List[Dict[str, Any]]:
    return _load_store()["profiles"]


def find_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    for profile in list_profiles():
        if profile.get("id") == profile_id:
            return profile
    return None


def find_profile_by_model(served_model_id: str) -> Optional[Dict[str, Any]]:
    for profile in list_profiles():
        if profile.get("served_model_id") == served_model_id:
            return profile
    return None


def upsert_profile(values: Dict[str, Any], profile_id: Optional[str] = None) -> Dict[str, Any]:
    store = _load_store()
    cfg = store["settings"]
    served = str(values.get("served_model_id") or "").strip()
    gguf = str(values.get("gguf_path") or "").strip()
    if not served:
        raise ValueError("served_model_id is required")
    if not gguf:
        raise ValueError("gguf_path is required")
    _validate_model_path(gguf, cfg)

    profiles = store["profiles"]
    existing = None
    existing_index = None
    for index, profile in enumerate(profiles):
        if profile_id and profile.get("id") == profile_id:
            existing = profile
            existing_index = index
        elif profile.get("served_model_id") == served:
            raise ValueError(f"served model id already exists: {served}")
    if profile_id and existing is None:
        raise KeyError("profile not found")

    runtime = _profile_runtime(values)
    if runtime.get("cache_enabled") and not runtime.get("cache_path"):
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in served).strip("-") or "model"
        runtime["cache_path"] = f"{str(cfg.get('cache_dir')).rstrip('/')}/{safe}"

    profile = dict(existing or {})
    profile.update(
        {
            "id": profile.get("id") or profile_id or uuid.uuid4().hex[:12],
            "served_model_id": served,
            "gguf_path": gguf,
            "port": int(values.get("port") or cfg.get("default_port") or 8081),
            "status": profile.get("status") or "stopped",
            **runtime,
        }
    )
    if existing is None:
        profiles.append(profile)
    elif existing_index is not None:
        profiles[existing_index] = profile
    store["profiles"] = profiles
    _write_store(store)
    return profile


def delete_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    store = _load_store()
    removed = None
    kept = []
    for profile in store["profiles"]:
        if profile.get("id") == profile_id:
            removed = profile
        else:
            kept.append(profile)
    if removed is None:
        return None
    if store.get("active_profile_id") == profile_id:
        store["active_profile_id"] = None
    store["profiles"] = kept
    _write_store(store)
    return removed


def pid_path(profile: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> str:
    provider_settings = cfg or get_provider_settings()
    return f"{str(provider_settings.get('runtime_dir')).rstrip('/')}/{profile['id']}.pid"


def log_path(profile: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> str:
    provider_settings = cfg or get_provider_settings()
    return f"{str(provider_settings.get('runtime_dir')).rstrip('/')}/{profile['id']}.log"


def _slot_cache_path(profile: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> str:
    provider_settings = cfg or get_provider_settings()
    raw_path = str(profile.get("cache_path") or "").strip()
    if raw_path:
        return raw_path
    safe = "".join(
        ch if ch.isalnum() or ch in "._-" else "-"
        for ch in str(profile.get("served_model_id") or profile.get("id") or "model")
    ).strip("-") or "model"
    return f"{str(provider_settings.get('cache_dir') or '/models/llama/.llm-agent-cache').rstrip('/')}/{safe}"


def build_llama_server_args(profile: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    provider_settings = cfg or get_provider_settings()
    args = [
        str(provider_settings.get("binary_path") or "/usr/local/bin/llama-server"),
        "--host",
        str(provider_settings.get("default_host") or "0.0.0.0"),
        "--port",
        str(profile.get("port") or provider_settings.get("default_port") or 8081),
        "--model",
        str(profile["gguf_path"]),
        "--alias",
        str(profile["served_model_id"]),
    ]
    flag_map = [
        ("ctx_size", "--ctx-size"),
        ("n_gpu_layers", "--n-gpu-layers"),
        ("main_gpu", "--main-gpu"),
        ("tensor_split", "--tensor-split"),
        ("split_mode", "--split-mode"),
        ("threads", "--threads"),
        ("threads_batch", "--threads-batch"),
        ("batch_size", "--batch-size"),
        ("ubatch_size", "--ubatch-size"),
        ("parallel", "--parallel"),
    ]
    for key, flag in flag_map:
        value = profile.get(key)
        if value not in (None, "", 0):
            args.extend([flag, str(value)])
    if profile.get("flash_attn"):
        args.append("--flash-attn")
    if profile.get("cont_batching"):
        args.append("--cont-batching")
    if profile.get("cache_enabled"):
        args.append("--cache-prompt")
        cache_ram = profile.get("cache_ram", 8192)
        if cache_ram not in (None, ""):
            args.extend(["--cache-ram", str(cache_ram)])
        if profile.get("cache_idle_slots", True):
            args.append("--cache-idle-slots")
        cache_reuse = profile.get("cache_reuse")
        if cache_reuse not in (None, "", 0):
            args.extend(["--cache-reuse", str(cache_reuse)])
        ctx_checkpoints = profile.get("ctx_checkpoints", 32)
        if ctx_checkpoints not in (None, ""):
            args.extend(["--ctx-checkpoints", str(ctx_checkpoints)])
        checkpoint_min_step = profile.get("checkpoint_min_step", 256)
        if checkpoint_min_step not in (None, ""):
            args.extend(["--checkpoint-min-step", str(checkpoint_min_step)])
        args.extend(["--slot-save-path", _slot_cache_path(profile, provider_settings)])
    for item in profile.get("extra_args") or []:
        if item is not None and str(item).strip():
            args.append(str(item))
    return args


def _set_profile_status(profile_id: str, status: str, error: Optional[str] = None) -> None:
    store = _load_store()
    for profile in store["profiles"]:
        if profile.get("id") == profile_id:
            profile["status"] = status
            if error:
                profile["last_error"] = error
            elif "last_error" in profile:
                profile.pop("last_error", None)
            break
    _write_store(store)


def status_for_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_provider_settings()
    pid_file = pid_path(profile, cfg)
    model = str(profile.get("gguf_path") or "")
    remote = (
        "pidfile={pid}; model={model}; "
        "if [ -f \"$pidfile\" ]; then "
        "pid=$(cat \"$pidfile\" 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null && tr '\\0' ' ' < /proc/$pid/cmdline | grep -F -- \"$model\" >/dev/null 2>&1; then "
        "echo running; exit 0; fi; fi; echo stopped"
    ).format(pid=shlex.quote(pid_file), model=shlex.quote(model))
    ok, output = run_ssh(remote)
    status = "unknown"
    if ok:
        status = "running" if "running" in output else "stopped"
    return {**profile, "status": status, "pid_path": pid_file, "log_path": log_path(profile, cfg)}


def _cleanup_runtime_pids_remote(cfg: Dict[str, Any], port: Optional[int] = None) -> str:
    runtime_dir = str(cfg.get("runtime_dir")).rstrip("/")
    port_block = ""
    if port:
        port_block = (
            "if command -v fuser >/dev/null 2>&1; then "
            "for pid in $(fuser -n tcp {port} 2>/dev/null); do "
            "if tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -F -- 'llama-server' >/dev/null 2>&1; then "
            "kill \"$pid\" 2>/dev/null || true; sleep 1; kill -0 \"$pid\" 2>/dev/null && kill -9 \"$pid\" 2>/dev/null || true; "
            "fi; done; "
            "elif command -v ss >/dev/null 2>&1; then "
            "for pid in $(ss -ltnp 2>/dev/null | sed -n 's/.*:{port} .*pid=\\([0-9][0-9]*\\).*/\\1/p'); do "
            "if tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -F -- 'llama-server' >/dev/null 2>&1; then "
            "kill \"$pid\" 2>/dev/null || true; sleep 1; kill -0 \"$pid\" 2>/dev/null && kill -9 \"$pid\" 2>/dev/null || true; "
            "fi; done; fi; "
        ).format(port=int(port))
    return (
        "runtime_dir={runtime_dir}; "
        "if [ -d \"$runtime_dir\" ]; then "
        "for pidfile in \"$runtime_dir\"/*.pid; do "
        "[ -f \"$pidfile\" ] || continue; "
        "[ \"$(basename \"$pidfile\")\" = \"hf-download.pid\" ] && continue; "
        "pid=$(cat \"$pidfile\" 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        "if tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -F -- 'llama-server' >/dev/null 2>&1; then "
        "kill \"$pid\" 2>/dev/null || true; sleep 1; kill -0 \"$pid\" 2>/dev/null && kill -9 \"$pid\" 2>/dev/null || true; "
        "fi; fi; "
        "rm -f \"$pidfile\"; "
        "done; fi; "
        "{port_block}"
    ).format(runtime_dir=shlex.quote(runtime_dir), port_block=port_block)


def cleanup_llama_cpp_runtime(port: Optional[int] = None) -> Dict[str, Any]:
    cfg = get_provider_settings()
    remote = _cleanup_runtime_pids_remote(cfg, port=port) + "echo cleaned"
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "llama.cpp runtime cleanup failed")
    return {"ok": True, "message": output or "cleaned"}


def start_profile(profile_id: str) -> Dict[str, Any]:
    profile = find_profile(profile_id)
    if not profile:
        raise ValueError("profile not found")
    cfg = get_provider_settings()

    store = _load_store()
    active_id = store.get("active_profile_id")
    if active_id and active_id != profile_id:
        try:
            stop_profile(active_id)
        except Exception:
            pass
    cleanup_llama_cpp_runtime(port=int(profile.get("port") or cfg.get("default_port") or 8081))

    args = build_llama_server_args(profile, cfg)
    runtime_dir = str(cfg.get("runtime_dir")).rstrip("/")
    cache_dir = str(cfg.get("cache_dir")).rstrip("/")
    slot_cache_path = _slot_cache_path(profile, cfg) if profile.get("cache_enabled") else cache_dir
    pid_file = pid_path(profile, cfg)
    out_log = log_path(profile, cfg)
    binary = str(cfg.get("binary_path"))
    model = str(profile.get("gguf_path"))
    command = shlex.join(args)
    remote = (
        "mkdir -p {runtime_dir} {cache_dir} {slot_cache_path} || exit 10; "
        "test -x {binary} || exit 11; "
        "test -f {model} || exit 12; "
        "nohup {command} > {log} 2>&1 & echo $! > {pid}; cat {pid}"
    ).format(
        runtime_dir=shlex.quote(runtime_dir),
        cache_dir=shlex.quote(cache_dir),
        slot_cache_path=shlex.quote(slot_cache_path),
        binary=shlex.quote(binary),
        model=shlex.quote(model),
        command=command,
        log=shlex.quote(out_log),
        pid=shlex.quote(pid_file),
    )
    _set_profile_status(profile_id, "starting")
    ok, output = run_ssh(remote, timeout=max(int(cfg.get("ssh_timeout") or 20), 30))
    if not ok:
        _set_profile_status(profile_id, "error", output)
        raise RuntimeError(output or "profile start failed")
    store = _load_store()
    store["active_profile_id"] = profile_id
    for item in store["profiles"]:
        if item.get("id") == profile_id:
            item["status"] = "starting"
            item["last_pid"] = output.splitlines()[-1] if output else ""
            item.pop("last_error", None)
    _write_store(store)
    return status_for_profile(find_profile(profile_id) or profile)


def stop_profile(profile_id: str) -> Dict[str, Any]:
    profile = find_profile(profile_id)
    if not profile:
        raise ValueError("profile not found")
    cfg = get_provider_settings()
    pid_file = pid_path(profile, cfg)
    model = str(profile.get("gguf_path") or "")
    remote = (
        "{cleanup} "
        "pidfile={pid}; model={model}; "
        "if [ -f \"$pidfile\" ]; then "
        "pid=$(cat \"$pidfile\" 2>/dev/null); "
        "if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null && tr '\\0' ' ' < /proc/$pid/cmdline | grep -F -- \"$model\" >/dev/null 2>&1; then "
        "kill \"$pid\"; sleep 1; kill -0 \"$pid\" 2>/dev/null && kill -9 \"$pid\" 2>/dev/null || true; fi; "
        "rm -f \"$pidfile\"; fi; echo stopped"
    ).format(
        cleanup=_cleanup_runtime_pids_remote(cfg, port=int(profile.get("port") or cfg.get("default_port") or 8081)),
        pid=shlex.quote(pid_file),
        model=shlex.quote(model),
    )
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "profile stop failed")
    store = _load_store()
    if store.get("active_profile_id") == profile_id:
        store["active_profile_id"] = None
    for item in store["profiles"]:
        if item.get("id") == profile_id:
            item["status"] = "stopped"
    _write_store(store)
    return status_for_profile(find_profile(profile_id) or profile)


def restart_profile(profile_id: str) -> Dict[str, Any]:
    stop_profile(profile_id)
    return start_profile(profile_id)


def get_profile_logs(profile_id: str, lines: int = 500) -> str:
    profile = find_profile(profile_id)
    if not profile:
        raise ValueError("profile not found")
    remote = "test -f {log} && tail -n {lines} {log} || true".format(
        log=shlex.quote(log_path(profile)),
        lines=int(lines),
    )
    ok, output = run_ssh(remote)
    if not ok:
        raise RuntimeError(output or "log read failed")
    return output


def profile_base_url(profile: Dict[str, Any]) -> str:
    cfg = get_provider_settings()
    host = str(cfg.get("ssh_host") or settings.LLM_HOST)
    return f"http://{host}:{int(profile.get('port') or cfg.get('default_port') or 8081)}"


def wait_until_ready(profile: Dict[str, Any], timeout: Optional[int] = None) -> bool:
    deadline = time.monotonic() + int(timeout or get_provider_settings().get("readiness_timeout") or 180)
    url = f"{profile_base_url(profile).rstrip('/')}/v1/models"
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.ok:
                _set_profile_status(str(profile["id"]), "running")
                return True
        except Exception:
            pass
        time.sleep(2)
    _set_profile_status(str(profile["id"]), "error", "readiness timeout")
    return False
